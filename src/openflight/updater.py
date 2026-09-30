"""Check GitHub for a newer OpenFlight and install it (``--update-check``).

Off by default. When the server runs with ``--update-check`` it asks git every
few hours whether ``origin/main`` (``--update-remote``/``--update-branch``)
has commits the Pi does not, and the touchscreen offers an "Update" button.
Nothing is installed until someone taps it on the unit itself.

An update is a fast-forward only. The Pi must be on the tracked branch with no
local commits and no local edits (an ``npm install`` that rewrote
``ui/package-lock.json`` is the one exception: that file is regenerated, so it
is restored before merging). Steps, in order:

1. ``git merge --ff-only`` to the fetched commit.
2. ``uv sync`` (with ``--extra camera`` when the server uses the camera).
3. ``npm ci`` in ``ui/``, only when ``package.json`` or its lockfile changed.
4. The UI is built into ``ui/dist.update`` and swapped in only when the build
   succeeds, so a failed build never leaves the kiosk without a UI.
5. A smoke check imports the new server.

If any step fails, the checkout, ``uv.lock``, Python packages, UI packages and
``ui/dist`` are put back to what was running, and the result says so. The
caller restarts the service afterwards either way.

From an SSH session::

    uv run python -m openflight.updater check
    sudo systemctl stop openflight
    uv run python -m openflight.updater apply
    sudo systemctl start openflight
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"
DEFAULT_CHECK_HOURS = 6.0
MAX_LISTED_COMMITS = 10
# Lockfiles that npm rewrites on its own; restored before a merge.
REGENERATED_FILES = ("ui/package-lock.json", "package-lock.json")
UI_STAGING_DIR = "dist.update"
UI_PREVIOUS_DIR = "dist.prev"

# Seconds. uv and npm on a Pi 5 over Wi-Fi can take several minutes.
GIT_TIMEOUT_S = 90.0
UV_SYNC_TIMEOUT_S = 1200.0
NPM_CI_TIMEOUT_S = 1800.0
UI_BUILD_TIMEOUT_S = 1200.0
SMOKE_TIMEOUT_S = 180.0

# Status states reported to the UI.
STATE_DISABLED = "disabled"
STATE_IDLE = "idle"
STATE_CHECKING = "checking"
STATE_UP_TO_DATE = "up_to_date"
STATE_AVAILABLE = "available"
STATE_UPDATING = "updating"
STATE_RESTARTING = "restarting"
STATE_FAILED = "failed"
STATE_ERROR = "error"


class UpdateError(RuntimeError):
    """A step failed; ``detail`` holds the tail of the command output."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class CommandResult:
    """Exit code and combined stdout/stderr of one command."""

    returncode: int
    output: str


Runner = Callable[[Sequence[str], Path, float, Optional[dict]], CommandResult]


def run_command(
    args: Sequence[str], cwd: Path, timeout_s: float, env: Optional[dict] = None
) -> CommandResult:
    """Run a command, merging stderr into stdout. Never raises on a non-zero exit."""
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(127, f"{args[0]}: command not found")
    except subprocess.TimeoutExpired as expired:
        output = expired.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return CommandResult(124, f"{output}\n{args[0]} timed out after {timeout_s:.0f} s")
    return CommandResult(completed.returncode, completed.stdout or "")


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def short_sha(sha: Optional[str]) -> Optional[str]:
    """First seven characters of a commit id, as git shows it."""
    return sha[:7] if sha else None


def _find_uv() -> str:
    """uv lives in ~/.local/bin on the Pi, which systemd leaves off PATH."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "uv"


@dataclass(frozen=True)
class UpdateConfig:
    """Where to update and what to install (from the server flags)."""

    project_dir: Path = REPO_ROOT
    remote: str = DEFAULT_REMOTE
    branch: str = DEFAULT_BRANCH
    python_extras: tuple[str, ...] = ()
    log_dir: Optional[Path] = None


@dataclass
class UpdateStatus:
    """What the UI shows. ``to_dict`` is the ``update_status`` socket payload."""

    enabled: bool = True
    state: str = STATE_IDLE
    remote: str = DEFAULT_REMOTE
    branch: str = DEFAULT_BRANCH
    current: Optional[str] = None
    current_date: Optional[str] = None
    latest: Optional[str] = None
    behind: int = 0
    commits: list[dict] = field(default_factory=list)
    checked_at: Optional[str] = None
    error: Optional[str] = None
    step: Optional[str] = None
    rolled_back: bool = False

    def to_dict(self) -> dict:
        """JSON-safe copy for the socket."""
        return {
            "enabled": self.enabled,
            "state": self.state,
            "remote": self.remote,
            "branch": self.branch,
            "current": self.current,
            "current_date": self.current_date,
            "latest": self.latest,
            "behind": self.behind,
            "commits": [dict(commit) for commit in self.commits],
            "checked_at": self.checked_at,
            "error": self.error,
            "step": self.step,
            "rolled_back": self.rolled_back,
        }


def disabled_status() -> dict:
    """Payload sent when the server runs without --update-check."""
    status = UpdateStatus(enabled=False, state=STATE_DISABLED)
    return status.to_dict()


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of :meth:`Updater.apply`; ``installed`` is None when it failed."""

    ok: bool
    previous: Optional[str]
    installed: Optional[str]
    error: Optional[str] = None
    detail: str = ""
    rolled_back: bool = False
    log_path: Optional[Path] = None


class Updater:
    """Fast-forward the checkout to the tracked branch, with rollback."""

    def __init__(
        self,
        config: UpdateConfig,
        runner: Runner = run_command,
        clock: Callable[[], float] = time.time,
    ):
        self.config = config
        self._run_command = runner
        self._clock = clock
        self._lock = threading.Lock()
        self._log_lines: list[str] = []
        self.status = UpdateStatus(remote=config.remote, branch=config.branch)

    # -- command helpers -------------------------------------------------

    @property
    def _root(self) -> Path:
        return self.config.project_dir

    @property
    def _upstream(self) -> str:
        return f"refs/remotes/{self.config.remote}/{self.config.branch}"

    def _run(self, args: Sequence[str], timeout_s: float, cwd: Optional[Path] = None) -> str:
        where = cwd or self._root
        result = self._run_command(args, where, timeout_s, None)
        self._log_lines.append(f"$ {' '.join(args)}  (in {where})")
        if result.output.strip():
            self._log_lines.append(result.output.rstrip())
        if result.returncode != 0:
            raise UpdateError(
                f"{' '.join(args[:3])} failed (exit {result.returncode})", _tail(result.output)
            )
        return result.output.strip()

    def _git(self, *args: str) -> str:
        return self._run(["git", *args], GIT_TIMEOUT_S)

    def _rev(self, ref: str) -> str:
        return self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")

    # -- check ------------------------------------------------------------

    def _preflight(self) -> tuple[str, str, int, int]:
        """Fetch and return (head, upstream, behind, ahead); raise if not updatable."""
        if self._git("rev-parse", "--is-inside-work-tree") != "true":
            raise UpdateError(f"{self._root} is not a git checkout")
        branch = self._run_command(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"], self._root, GIT_TIMEOUT_S, None
        ).output.strip()
        if branch != self.config.branch:
            shown = branch or "a detached commit"
            raise UpdateError(
                f"The Pi is on {shown}, not {self.config.branch}; update it over SSH instead"
            )
        self._git("fetch", "--quiet", self.config.remote, self.config.branch)
        head = self._rev("HEAD")
        upstream = self._rev(self._upstream)
        behind = int(self._git("rev-list", "--count", f"{head}..{upstream}") or 0)
        ahead = int(self._git("rev-list", "--count", f"{upstream}..{head}") or 0)
        return head, upstream, behind, ahead

    def read_local_version(self) -> None:
        """Fill in the installed commit (no network), so the menu shows it before a check."""
        try:
            head = self._rev("HEAD")
            self.status.current = short_sha(head)
            self.status.current_date = self._git("log", "-1", "--format=%cs", head) or None
        except UpdateError as error:
            logger.warning("[UPDATE] Could not read the installed version: %s", error)

    def check(self) -> UpdateStatus:
        """Fetch the tracked branch and report whether an update is available."""
        if not self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            return self.status
        try:
            self._log_lines = []
            self.status.state = STATE_CHECKING
            self.status.error = None
            try:
                head, upstream, behind, ahead = self._preflight()
                commits = []
                if behind:
                    listing = self._git(
                        "log",
                        f"--max-count={MAX_LISTED_COMMITS}",
                        "--format=%h%x09%s",
                        f"{head}..{upstream}",
                    )
                    for line in listing.splitlines():
                        sha, _, subject = line.partition("\t")
                        commits.append({"sha": sha, "subject": subject})
                self.status.current = short_sha(head)
                self.status.current_date = self._git("log", "-1", "--format=%cs", head) or None
                self.status.latest = short_sha(upstream)
                self.status.behind = behind
                self.status.commits = commits
                if ahead:
                    self.status.state = STATE_ERROR
                    self.status.error = (
                        f"The Pi has {ahead} local commit(s) not on "
                        f"{self.config.remote}/{self.config.branch}; update it over SSH instead"
                    )
                else:
                    self.status.state = STATE_AVAILABLE if behind else STATE_UP_TO_DATE
            except UpdateError as error:
                self.status.state = STATE_ERROR
                self.status.error = str(error)
                logger.warning("[UPDATE] Check failed: %s %s", error, error.detail)
            self.status.checked_at = datetime.fromtimestamp(
                self._clock(), tz=timezone.utc
            ).isoformat(timespec="seconds")
            return self.status
        finally:
            self._lock.release()

    # -- apply ------------------------------------------------------------

    def _dirty_files(self) -> list[str]:
        """Tracked files with staged or unstaged changes (untracked files are ignored)."""
        return [line for line in self._git("diff", "--name-only", "HEAD").splitlines() if line]

    def _changed(self, old: str, new: str, *paths: str) -> bool:
        return bool(self._git("diff", "--name-only", old, new, "--", *paths))

    def _uv_sync(self, *extra: str) -> None:
        args = [_find_uv(), "sync", *extra]
        for name in self.config.python_extras:
            args += ["--extra", name]
        self._run(args, UV_SYNC_TIMEOUT_S)

    def _npm(self, *args: str, timeout_s: float) -> None:
        self._run(["npm", *args], timeout_s, cwd=self._root / "ui")

    def _write_log(self) -> Optional[Path]:
        if not self.config.log_dir:
            return None
        try:
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(self._clock()).strftime("%Y%m%d_%H%M%S")
            path = self.config.log_dir / f"update_{stamp}.log"
            path.write_text("\n".join(self._log_lines) + "\n", encoding="utf-8")
            return path
        except OSError:
            logger.warning("[UPDATE] Could not write the update log", exc_info=True)
            return None

    def preflight_error(self) -> Optional[str]:
        """Why an update cannot start right now (checked before stopping anything)."""
        if self._lock.locked():
            return "An update check is already running"
        if self.status.state != STATE_AVAILABLE:
            return "No update is available; check for updates first"
        return None

    def apply(self, progress: Callable[[str], None] = lambda _step: None) -> ApplyResult:
        """Install the fetched update. Always leaves a runnable checkout behind."""
        with self._lock, _repo_lock(self._root):
            self._log_lines = []
            self.status.rolled_back = False
            self.status.error = None
            result = self._apply_locked(progress)
            log_path = self._write_log()
            if result.ok:
                self.status.current = short_sha(result.installed)
                self.status.behind = 0
                self.status.commits = []
            else:
                self.status.error = result.error
                self.status.rolled_back = result.rolled_back
            return replace(result, log_path=log_path)

    def _apply_locked(self, progress: Callable[[str], None]) -> ApplyResult:
        root = self._root
        ui = root / "ui"
        try:
            progress("Checking the download")
            head, upstream, behind, ahead = self._preflight()
            if ahead:
                raise UpdateError("The Pi has local commits; update it over SSH instead")
            if not behind:
                return ApplyResult(True, head, head)
            dirty = self._dirty_files()
            regenerated = [path for path in dirty if path in REGENERATED_FILES]
            others = [path for path in dirty if path not in REGENERATED_FILES]
            if others:
                raise UpdateError(
                    "The Pi has local edits (" + ", ".join(others[:5]) + "); update it over SSH"
                )
            if regenerated:
                self._git("checkout", "--", *regenerated)
        except UpdateError as error:
            return ApplyResult(False, None, None, str(error), error.detail)

        lock_file = root / "uv.lock"
        lock_backup = root / "uv.lock.update-backup"
        staging = ui / UI_STAGING_DIR
        dist = ui / "dist"
        previous = ui / UI_PREVIOUS_DIR
        ui_swapped = False
        npm_ran = False
        try:
            if lock_file.exists():
                shutil.copy2(lock_file, lock_backup)
            progress("Downloading the update")
            self._git("merge", "--ff-only", "--quiet", upstream)
            progress("Installing Python packages")
            self._uv_sync()
            ui_changed = self._changed(head, upstream, "ui")
            if self._changed(head, upstream, "ui/package.json", "ui/package-lock.json"):
                progress("Installing interface packages")
                npm_ran = True
                self._npm("ci", timeout_s=NPM_CI_TIMEOUT_S)
            if ui_changed or not (dist / "index.html").is_file():
                progress("Building the interface")
                shutil.rmtree(staging, ignore_errors=True)
                self._npm(
                    "run",
                    "build",
                    "--",
                    "--outDir",
                    UI_STAGING_DIR,
                    "--emptyOutDir",
                    timeout_s=UI_BUILD_TIMEOUT_S,
                )
                if not (staging / "index.html").is_file():
                    raise UpdateError(f"The interface build did not produce ui/{UI_STAGING_DIR}")
                shutil.rmtree(previous, ignore_errors=True)
                if dist.exists():
                    dist.rename(previous)
                staging.rename(dist)
                ui_swapped = True
            progress("Checking the new version")
            self._run(
                [_find_uv(), "run", "--no-sync", "python", "-c", "import openflight.server"],
                SMOKE_TIMEOUT_S,
            )
        except UpdateError as error:
            progress("Rolling back")
            rolled_back = self._rollback(head, lock_backup, npm_ran, ui_swapped)
            return ApplyResult(False, head, None, str(error), error.detail, rolled_back=rolled_back)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        shutil.rmtree(previous, ignore_errors=True)
        lock_backup.unlink(missing_ok=True)
        return ApplyResult(True, head, upstream)

    def _rollback(self, head: str, lock_backup: Path, npm_ran: bool, ui_swapped: bool) -> bool:
        """Put back the commit, lockfile, packages and UI that were running."""
        root = self._root
        dist = root / "ui" / "dist"
        previous = root / "ui" / UI_PREVIOUS_DIR
        ok = True
        steps: list[tuple[str, Callable[[], None]]] = [
            ("checkout", lambda: self._git("reset", "--hard", "--quiet", head)),
        ]
        if lock_backup.exists():
            # The restored lockfile pins exactly what was running.
            steps.append(("uv.lock", lambda: shutil.move(str(lock_backup), str(root / "uv.lock"))))
            steps.append(("python packages", lambda: self._uv_sync("--frozen")))
        else:
            # No lockfile before the update: drop the new one and resolve again.
            steps.append(("uv.lock", lambda: (root / "uv.lock").unlink(missing_ok=True)))
            steps.append(("python packages", self._uv_sync))
        if npm_ran:
            steps.append(("ui packages", lambda: self._npm("ci", timeout_s=NPM_CI_TIMEOUT_S)))
        if ui_swapped and previous.exists():

            def restore_ui() -> None:
                shutil.rmtree(dist, ignore_errors=True)
                previous.rename(dist)

            steps.append(("ui build", restore_ui))
        for name, step in steps:
            try:
                step()
            except (UpdateError, OSError) as error:
                ok = False
                logger.error("[UPDATE] Rollback of %s failed: %s", name, error)
                self._log_lines.append(f"rollback of {name} failed: {error}")
        return ok


@contextmanager
def _repo_lock(project_dir: Path) -> Iterator[None]:
    """Keep the server and an SSH session from updating the same checkout at once."""
    try:
        import fcntl  # pylint: disable=import-outside-toplevel
    except ImportError:  # pragma: no cover - not a Pi
        yield
        return
    git_dir = project_dir / ".git"
    lock_path = (git_dir if git_dir.is_dir() else project_dir) / "openflight-update.lock"
    with open(lock_path, "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise UpdateError("Another update is already running") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def add_update_args(parser: argparse.ArgumentParser) -> None:
    """Server flags for the update check (all off/unchanged by default)."""
    group = parser.add_argument_group("software updates")
    group.add_argument(
        "--update-check",
        action="store_true",
        help=(
            "Check GitHub for a newer OpenFlight and offer an Update button on the "
            "touchscreen. Nothing installs until it is tapped on the unit. Off by default."
        ),
    )
    group.add_argument(
        "--update-remote",
        default=DEFAULT_REMOTE,
        help=f"Git remote to update from (default: {DEFAULT_REMOTE})",
    )
    group.add_argument(
        "--update-branch",
        default=DEFAULT_BRANCH,
        help=f"Branch to track; the Pi must be on it (default: {DEFAULT_BRANCH})",
    )
    group.add_argument(
        "--update-check-hours",
        type=float,
        default=DEFAULT_CHECK_HOURS,
        help=f"Hours between automatic checks (default: {DEFAULT_CHECK_HOURS:g})",
    )


def main(argv: Optional[Sequence[str]] = None, runner: Runner = run_command) -> int:
    """``python -m openflight.updater check|apply`` for SSH sessions."""
    parser = argparse.ArgumentParser(prog="python -m openflight.updater", description=__doc__)
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--camera", action="store_true", help="Also sync the camera extra")
    parser.add_argument("--json", action="store_true", help="Print the status as JSON")
    parser.add_argument("--project-dir", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "openflight_logs")
    args = parser.parse_args(argv)

    updater = Updater(
        UpdateConfig(
            project_dir=args.project_dir,
            remote=args.remote,
            branch=args.branch,
            python_extras=("camera",) if args.camera else (),
            log_dir=args.log_dir,
        ),
        runner=runner,
    )
    status = updater.check()
    if args.json:
        print(json.dumps(status.to_dict(), indent=2))
    else:
        print(f"Installed: {status.current} ({status.current_date})")
        if status.state == STATE_AVAILABLE:
            print(f"{status.behind} new commit(s) on {args.remote}/{args.branch}:")
            for commit in status.commits:
                print(f"  {commit['sha']}  {commit['subject']}")
        elif status.state == STATE_UP_TO_DATE:
            print("Up to date.")
        else:
            print(f"Cannot update: {status.error}")
    if args.command == "check" or status.state != STATE_AVAILABLE:
        return 0 if status.state in (STATE_AVAILABLE, STATE_UP_TO_DATE) else 1

    try:
        result = updater.apply(lambda step: print(f"... {step}", flush=True))
    except UpdateError as error:
        print(f"Update not started: {error}", file=sys.stderr)
        return 1
    if result.log_path:
        print(f"Log: {result.log_path}")
    if result.ok:
        print(f"Updated {short_sha(result.previous)} -> {short_sha(result.installed)}.")
        print("Restart to use it: sudo systemctl restart openflight")
        return 0
    print(f"Update failed: {result.error}", file=sys.stderr)
    if result.detail:
        print(result.detail, file=sys.stderr)
    print(
        "Rolled back to the previous version."
        if result.rolled_back
        else "Rollback was incomplete; see the log above.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
