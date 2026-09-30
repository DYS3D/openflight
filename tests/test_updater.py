"""openflight.updater against real git repositories, with uv and npm stubbed."""

import subprocess
import sys
from pathlib import Path

import pytest

from openflight import updater as upd


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class Repos:
    """A bare 'GitHub', a developer clone that pushes, and the Pi's clone."""

    def __init__(self, root: Path):
        self.origin = root / "origin.git"
        self.dev = root / "dev"
        self.pi = root / "pi"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.dev)], check=True)
        self.commit(
            "initial",
            {
                ".gitignore": "ui/dist\nui/dist.update\nui/dist.prev\nuv.lock\nuv.lock.update-backup\n",
                "pyproject.toml": "[project]\nname='x'\n",
                "src/app.py": "VERSION = 1\n",
                "ui/package.json": '{"name": "ui"}\n',
                "ui/package-lock.json": '{"lockfileVersion": 3}\n',
                "ui/src/main.ts": "export const v = 1;\n",
            },
        )
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.pi)], check=True)
        (self.pi / "ui" / "dist").mkdir(parents=True)
        (self.pi / "ui" / "dist" / "index.html").write_text("old ui")
        (self.pi / "uv.lock").write_text("lock v1")

    def commit(self, message: str, files: dict[str, str]) -> str:
        for name, content in files.items():
            path = self.dev / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        _git(self.dev, "add", "-A")
        _git(self.dev, "commit", "-q", "-m", message)
        _git(self.dev, "push", "-q", "origin", "HEAD:main")
        return _git(self.dev, "rev-parse", "HEAD")

    def pi_head(self) -> str:
        return _git(self.pi, "rev-parse", "HEAD")


class FakeTools:
    """Runs git for real; records uv/npm and fails the named steps once."""

    def __init__(self):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.fail_once: set[str] = set()
        self.fail_always: set[str] = set()

    def __call__(self, args, cwd, timeout_s, env):
        tool = Path(args[0]).name
        if tool == "git":
            return upd.run_command(args, cwd, timeout_s, env)
        rest = tuple(args[1:])
        key = {
            ("uv", "sync"): "uv sync",
            ("uv", "run"): "smoke",
            ("npm", "ci"): "npm ci",
            ("npm", "run"): "build",
        }[(tool, rest[0])]
        self.calls.append((key, rest))
        if key in self.fail_always or key in self.fail_once:
            self.fail_once.discard(key)
            return upd.CommandResult(1, f"{key} broke\nlast line")
        if key == "build":
            out = Path(cwd) / rest[rest.index("--outDir") + 1]
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.html").write_text("new ui")
        if key == "uv sync" and "--frozen" not in rest:  # --frozen never rewrites the lock
            (Path(cwd) / "uv.lock").write_text("lock v2")
        return upd.CommandResult(0, "")

    def keys(self) -> list[str]:
        return [key for key, _ in self.calls]


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")
    return Repos(tmp_path)


@pytest.fixture
def tools():
    return FakeTools()


def _updater(repos, tools, **config):
    return upd.Updater(
        upd.UpdateConfig(project_dir=repos.pi, log_dir=repos.pi.parent / "logs", **config),
        runner=tools,
        clock=lambda: 1_790_000_000.0,
    )


class TestLocalVersion:
    def test_reads_head_without_fetching(self, repos, tools):
        _git(repos.pi, "remote", "set-url", "origin", str(repos.pi.parent / "offline.git"))
        updater = _updater(repos, tools)
        updater.read_local_version()
        assert updater.status.current == repos.pi_head()[:7]
        assert updater.status.current_date and updater.status.state == upd.STATE_IDLE

    def test_not_a_checkout_is_logged_not_raised(self, tmp_path, tools):
        updater = upd.Updater(upd.UpdateConfig(project_dir=tmp_path), runner=tools)
        updater.read_local_version()
        assert updater.status.current is None


class TestCheck:
    def test_up_to_date(self, repos, tools):
        status = _updater(repos, tools).check()
        assert status.state == upd.STATE_UP_TO_DATE
        assert status.current == repos.pi_head()[:7]
        assert status.behind == 0 and status.commits == []
        assert status.checked_at.startswith("2026-")
        assert tools.calls == []  # a check never runs uv or npm

    def test_new_commits_are_listed_newest_first(self, repos, tools):
        repos.commit("first fix", {"src/app.py": "VERSION = 2\n"})
        latest = repos.commit("second fix", {"src/app.py": "VERSION = 3\n"})
        status = _updater(repos, tools).check()
        assert status.state == upd.STATE_AVAILABLE
        assert status.behind == 2
        assert status.latest == latest[:7]
        assert [c["subject"] for c in status.commits] == ["second fix", "first fix"]
        assert status.to_dict()["commits"][0]["sha"] == latest[:7]

    def test_other_branch_is_refused(self, repos, tools):
        _git(repos.pi, "checkout", "-q", "-b", "experiment")
        status = _updater(repos, tools).check()
        assert status.state == upd.STATE_ERROR
        assert "experiment" in status.error

    def test_local_commits_are_refused(self, repos, tools):
        repos.commit("upstream", {"src/app.py": "VERSION = 2\n"})
        (repos.pi / "local.txt").write_text("mine")
        _git(repos.pi, "add", "local.txt")
        _git(repos.pi, "commit", "-q", "-m", "local")
        status = _updater(repos, tools).check()
        assert status.state == upd.STATE_ERROR
        assert "local commit" in status.error

    def test_fetch_failure_is_reported_not_raised(self, repos, tools):
        _git(repos.pi, "remote", "set-url", "origin", str(repos.pi.parent / "missing.git"))
        status = _updater(repos, tools).check()
        assert status.state == upd.STATE_ERROR
        assert "git fetch" in status.error


class TestApply:
    def test_python_only_update(self, repos, tools):
        new = repos.commit("server fix", {"src/app.py": "VERSION = 2\n"})
        updater = _updater(repos, tools, python_extras=("camera",))
        assert updater.check().state == upd.STATE_AVAILABLE
        steps = []
        result = updater.apply(steps.append)
        assert result.ok and result.installed == new
        assert repos.pi_head() == new
        # UI untouched: no npm, no build, same bundle.
        assert tools.keys() == ["uv sync", "smoke"]
        assert tools.calls[0][1] == ("sync", "--extra", "camera")
        assert (repos.pi / "ui" / "dist" / "index.html").read_text() == "old ui"
        assert not (repos.pi / "uv.lock.update-backup").exists()
        assert steps[0] == "Checking the download" and "Checking the new version" in steps
        assert result.log_path and result.log_path.name.startswith("update_")
        assert updater.status.behind == 0

    def test_ui_update_builds_in_staging_and_swaps(self, repos, tools):
        new = repos.commit(
            "ui change",
            {"ui/src/main.ts": "export const v = 2;\n", "ui/package-lock.json": '{"v": 4}\n'},
        )
        result = _updater(repos, tools).apply()
        assert result.ok and repos.pi_head() == new
        assert tools.keys() == ["uv sync", "npm ci", "build", "smoke"]
        dist = repos.pi / "ui" / "dist"
        assert (dist / "index.html").read_text() == "new ui"
        assert not (repos.pi / "ui" / upd.UI_PREVIOUS_DIR).exists()
        assert not (repos.pi / "ui" / upd.UI_STAGING_DIR).exists()

    def test_ui_source_change_without_new_packages_skips_npm_ci(self, repos, tools):
        repos.commit("ui tweak", {"ui/src/main.ts": "export const v = 2;\n"})
        assert _updater(repos, tools).apply().ok
        assert tools.keys() == ["uv sync", "build", "smoke"]

    def test_missing_ui_bundle_is_rebuilt(self, repos, tools):
        repos.commit("server fix", {"src/app.py": "VERSION = 2\n"})
        (repos.pi / "ui" / "dist" / "index.html").unlink()
        assert _updater(repos, tools).apply().ok
        assert "build" in tools.keys()

    def test_failed_build_rolls_everything_back(self, repos, tools):
        old = repos.pi_head()
        repos.commit(
            "broken ui",
            {"ui/src/main.ts": "oops\n", "ui/package-lock.json": '{"v": 5}\n'},
        )
        tools.fail_once.add("build")
        steps = []
        result = _updater(repos, tools).apply(steps.append)
        assert not result.ok and result.rolled_back
        assert "npm run build" in result.error and "last line" in result.detail
        assert repos.pi_head() == old
        assert (repos.pi / "uv.lock").read_text() == "lock v1"
        assert (repos.pi / "ui" / "dist" / "index.html").read_text() == "old ui"
        # Packages reinstalled from the restored lockfiles.
        assert tools.keys() == ["uv sync", "npm ci", "build", "uv sync", "npm ci"]
        assert tools.calls[3][1] == ("sync", "--frozen")
        assert steps[-1] == "Rolling back"
        assert not (repos.pi / "ui" / upd.UI_STAGING_DIR).exists()

    def test_failed_smoke_check_restores_the_previous_bundle(self, repos, tools):
        old = repos.pi_head()
        repos.commit("bad server", {"ui/src/main.ts": "export const v = 9;\n"})
        tools.fail_once.add("smoke")
        result = _updater(repos, tools).apply()
        assert not result.ok and result.rolled_back
        assert repos.pi_head() == old
        assert (repos.pi / "ui" / "dist" / "index.html").read_text() == "old ui"
        assert not (repos.pi / "ui" / upd.UI_PREVIOUS_DIR).exists()

    def test_failed_uv_sync_restores_the_lockfile(self, repos, tools):
        old = repos.pi_head()
        repos.commit("new dependency", {"pyproject.toml": "[project]\nname='y'\n"})
        tools.fail_once.add("uv sync")
        result = _updater(repos, tools).apply()
        assert not result.ok and result.rolled_back
        assert repos.pi_head() == old
        assert (repos.pi / "uv.lock").read_text() == "lock v1"

    def test_incomplete_rollback_is_reported(self, repos, tools):
        repos.commit("new dependency", {"pyproject.toml": "[project]\nname='z'\n"})
        tools.fail_always.add("uv sync")
        result = _updater(repos, tools).apply()
        assert not result.ok and not result.rolled_back

    def test_local_edits_block_the_update_without_changing_anything(self, repos, tools):
        old = repos.pi_head()
        repos.commit("fix", {"src/app.py": "VERSION = 2\n"})
        (repos.pi / "src" / "app.py").write_text("VERSION = 'hacked'\n")
        result = _updater(repos, tools).apply()
        assert not result.ok and "local edits" in result.error and "src/app.py" in result.error
        assert repos.pi_head() == old and tools.calls == []
        assert (repos.pi / "src" / "app.py").read_text() == "VERSION = 'hacked'\n"

    def test_regenerated_package_lock_is_restored_first(self, repos, tools):
        new = repos.commit("fix", {"src/app.py": "VERSION = 2\n"})
        (repos.pi / "ui" / "package-lock.json").write_text('{"rewritten": true}\n')
        assert _updater(repos, tools).apply().ok
        assert repos.pi_head() == new

    def test_nothing_to_do_is_a_no_op(self, repos, tools):
        result = _updater(repos, tools).apply()
        assert result.ok and result.previous == result.installed
        assert tools.calls == []

    def test_second_apply_is_refused_while_one_holds_the_repo_lock(self, repos, tools):
        with upd._repo_lock(repos.pi):
            with pytest.raises(upd.UpdateError, match="already running"):
                _updater(repos, tools).apply()


class TestPreflightError:
    def test_requires_an_available_update(self, repos, tools):
        updater = _updater(repos, tools)
        assert "check for updates" in updater.preflight_error()
        repos.commit("fix", {"src/app.py": "VERSION = 2\n"})
        updater.check()
        assert updater.preflight_error() is None


class TestRunCommand:
    def test_missing_binary(self, tmp_path):
        assert upd.run_command(["definitely-not-a-command-xyz"], tmp_path, 5).returncode == 127

    def test_timeout(self, tmp_path):
        result = upd.run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, 0.2
        )
        assert result.returncode == 124 and "timed out" in result.output


class TestArgs:
    def test_defaults_are_off(self):
        import argparse

        parser = argparse.ArgumentParser()
        upd.add_update_args(parser)
        args = parser.parse_args([])
        assert args.update_check is False
        assert (args.update_remote, args.update_branch) == ("origin", "main")
        assert args.update_check_hours == upd.DEFAULT_CHECK_HOURS

    def test_disabled_payload(self):
        payload = upd.disabled_status()
        assert payload["enabled"] is False and payload["state"] == upd.STATE_DISABLED


class TestCli:
    def _args(self, repos, *extra):
        return [*extra, "--project-dir", str(repos.pi), "--log-dir", str(repos.pi.parent / "logs")]

    def test_check_prints_available_commits(self, repos, tools, capsys):
        repos.commit("cli visible fix", {"src/app.py": "VERSION = 2\n"})
        assert upd.main(self._args(repos, "check"), runner=tools) == 0
        out = capsys.readouterr().out
        assert "1 new commit(s)" in out and "cli visible fix" in out
        assert tools.calls == []

    def test_check_json(self, repos, tools, capsys):
        assert upd.main(self._args(repos, "check", "--json"), runner=tools) == 0
        assert '"state": "up_to_date"' in capsys.readouterr().out

    def test_apply_runs_and_reports(self, repos, tools, capsys):
        new = repos.commit("cli fix", {"src/app.py": "VERSION = 2\n"})
        assert upd.main(self._args(repos, "apply", "--camera"), runner=tools) == 0
        assert repos.pi_head() == new
        assert tools.calls[0][1] == ("sync", "--extra", "camera")
        assert "sudo systemctl restart openflight" in capsys.readouterr().out

    def test_apply_failure_exits_nonzero(self, repos, tools, capsys):
        repos.commit("cli fix", {"src/app.py": "VERSION = 2\n"})
        tools.fail_once.add("smoke")
        assert upd.main(self._args(repos, "apply"), runner=tools) == 1
        assert "Rolled back" in capsys.readouterr().err

    def test_unusable_checkout_exits_nonzero(self, repos, tools, capsys):
        _git(repos.pi, "checkout", "-q", "-b", "other")
        assert upd.main(self._args(repos, "apply"), runner=tools) == 1
        assert "Cannot update" in capsys.readouterr().out


def test_rollback_without_a_previous_lockfile_resolves_again(repos, tools):
    repos.commit("fix", {"ui/src/main.ts": "export const v = 3;\n"})
    (repos.pi / "uv.lock").unlink()
    tools.fail_once.add("build")
    result = _updater(repos, tools).apply()
    assert not result.ok and result.rolled_back
    assert tools.calls[-1] == ("uv sync", ("sync",))
