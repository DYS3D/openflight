"""Spool-and-retry mechanics — the session directory *is* the queue.

State lives in sidecar files next to each ``session_*.jsonl`` so it survives
crashes with no database:

- ``<session>.jsonl.pushed`` — present once accepted by the server (terminal).
- ``<session>.jsonl.parked`` — present once given up on (terminal).
- ``<session>.jsonl.state`` — JSON attempt counter + last error for in-flight
  retries; removed on success.

With raw uploads on, the ``.pushed`` marker also carries the session's raw
capture queue (``captures_pending`` / ``captures_parked``): each shot's L3 dump
uploads after the session itself, so a pushed session may still have dumps
waiting. Keeping that queue in the marker means the (large) session file is
never re-read just to find its dumps.

Originals are never moved or modified.
"""

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

try:
    import fcntl
except ImportError:  # Windows: only for running the server on a dev PC
    fcntl = None  # type: ignore[assignment]

PUSHED_SUFFIX = ".pushed"
PARKED_SUFFIX = ".parked"
STATE_SUFFIX = ".state"

SESSION_GLOB = "session_*.jsonl"
LOCK_FILENAME = ".cloud-push.lock"

# After this many failures, park the file and report via ``status`` instead of
# retrying forever.
MAX_ATTEMPTS = 20

# How long to defer a session after a quota (402) rejection — retry daily
# rather than every timer tick.
QUOTA_COOLDOWN_S = 24 * 60 * 60

# A session file without a final ``session_end`` entry that changed this
# recently is taken to be still open in the server, which only writes
# ``session_end`` when the monitor stops. Older ones are from a crash and push.
IN_PROGRESS_GRACE_S = 30 * 60
_TAIL_BYTES = 4096


def _sidecar(path: Path, suffix: str) -> Path:
    # Append (not replace) so "session_x.jsonl" -> "session_x.jsonl.pushed".
    return path.with_name(path.name + suffix)


def _now() -> str:
    return datetime.now().isoformat()


def session_files(log_dir: Path) -> List[Path]:
    """All session JSONL files in ``log_dir`` (sorted); empty if dir missing."""
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []
    return sorted(log_dir.glob(SESSION_GLOB))


def is_pushed(path: Path) -> bool:
    """True if a ``.pushed`` marker exists for this session."""
    return _sidecar(path, PUSHED_SUFFIX).exists()


def is_parked(path: Path) -> bool:
    """True if a ``.parked`` marker exists for this session."""
    return _sidecar(path, PARKED_SUFFIX).exists()


def pending_sessions(log_dir: Path) -> List[Path]:
    """Session files that are neither pushed nor parked."""
    return [p for p in session_files(log_dir) if not is_pushed(p) and not is_parked(p)]


def _held_by_writer(path: Path) -> bool:
    """True while the server holds the session file's lock (see session_logger)."""
    try:
        import fcntl  # pylint: disable=import-outside-toplevel
    except ImportError:  # pragma: no cover - not a Pi
        return False
    try:
        with open(path, "rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return False


def is_in_progress(path: Path, now: Optional[float] = None) -> bool:
    """True if the session is still (or probably still) being written by the server."""
    if _held_by_writer(path):
        return True
    now = time.time() if now is None else now
    try:
        if now - path.stat().st_mtime >= IN_PROGRESS_GRACE_S:
            return False
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - _TAIL_BYTES))
            lines = handle.read().splitlines()
    except OSError:
        return False
    if not lines:
        return True
    try:
        return json.loads(lines[-1]).get("type") != "session_end"
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return True


def read_attempts(path: Path) -> int:
    """Number of recorded failed attempts for this session (0 if none)."""
    state_path = _sidecar(path, STATE_SUFFIX)
    if not state_path.exists():
        return 0
    try:
        return int(json.loads(state_path.read_text()).get("attempts", 0))
    except (json.JSONDecodeError, ValueError):
        return 0


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Replace ``path`` atomically so a crash never leaves a truncated marker."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def push_lock(log_dir: Path) -> Iterator[bool]:
    """Hold the per-directory upload lock; yields False if another push has it.

    The session-end hook, the systemd timer, and the UI button can all start a
    push. Without this they upload the same sessions twice and race on markers.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        # No flock on Windows; a dev PC never runs the timer and the kiosk together.
        yield True
        return
    with open(log_dir / LOCK_FILENAME, "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_failure(path: Path, error: str) -> int:
    """Increment the attempt counter, store the error, and park if maxed out.

    Returns the new attempt count.
    """
    attempts = read_attempts(path) + 1
    _write_json(
        _sidecar(path, STATE_SUFFIX),
        {"attempts": attempts, "last_error": error, "last_attempt_at": _now()},
    )
    if attempts >= MAX_ATTEMPTS:
        mark_parked(path, reason="max_attempts", attempts=attempts, last_error=error)
    return attempts


def record_cooldown(path: Path, reason: str, seconds: float, now: Optional[float] = None) -> None:
    """Defer retries for this session until ``seconds`` from now (e.g. quota).

    Does not increment the failure counter or park — quota is not a client bug.
    """
    now = time.time() if now is None else now
    state_path = _sidecar(path, STATE_SUFFIX)
    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, ValueError):
            state = {}
    state.update(
        {"cooldown_until": now + seconds, "cooldown_reason": reason, "last_attempt_at": _now()}
    )
    _write_json(state_path, state)


def in_cooldown(path: Path, now: Optional[float] = None) -> bool:
    """True if this session is deferred (cooldown not yet elapsed)."""
    now = time.time() if now is None else now
    state_path = _sidecar(path, STATE_SUFFIX)
    if not state_path.exists():
        return False
    try:
        until = json.loads(state_path.read_text()).get("cooldown_until")
    except (json.JSONDecodeError, ValueError):
        return False
    return until is not None and now < until


def mark_pushed(
    path: Path,
    session_id: str,
    shot_count: Optional[int],
    captures: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Mark a session as successfully uploaded and clear retry state.

    ``captures`` (raw mode) queues the session's capture files for upload.
    """
    marker: Dict[str, Any] = {
        "session_id": session_id,
        "shot_count": shot_count,
        "pushed_at": _now(),
    }
    if captures:
        marker["captures_pending"] = [dict(c, attempts=0) for c in captures]
        marker["captures_parked"] = []
    _write_json(_sidecar(path, PUSHED_SUFFIX), marker)
    state_path = _sidecar(path, STATE_SUFFIX)
    if state_path.exists():
        state_path.unlink()


def mark_parked(path: Path, reason: str, attempts: int, last_error: Optional[str]) -> None:
    """Mark a session as parked (given up on); reported via ``status``."""
    _write_json(
        _sidecar(path, PARKED_SUFFIX),
        {
            "reason": reason,
            "attempts": attempts,
            "last_error": last_error,
            "parked_at": _now(),
        },
    )


def clear_markers(path: Path, include_pushed: bool = False) -> List[str]:
    """Remove terminal/retry markers so a session becomes pending again.

    Clears ``.parked`` and ``.state`` (un-park + reset attempts/cooldown). With
    ``include_pushed`` also clears ``.pushed`` to force re-upload of a session
    the server already stored (idempotent server-side). Returns the suffixes
    actually removed.
    """
    suffixes = [PARKED_SUFFIX, STATE_SUFFIX]
    if include_pushed:
        suffixes.append(PUSHED_SUFFIX)
    cleared: List[str] = []
    for suffix in suffixes:
        marker = _sidecar(path, suffix)
        if marker.exists():
            marker.unlink()
            cleared.append(suffix)
    return cleared


def read_pushed(path: Path) -> Dict[str, Any]:
    """The ``.pushed`` marker contents ({} if absent or unreadable)."""
    try:
        data = json.loads(_sidecar(path, PUSHED_SUFFIX).read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def pending_captures(path: Path) -> List[Dict[str, Any]]:
    """Capture files still waiting to upload for a pushed session."""
    return list(read_pushed(path).get("captures_pending") or [])


def save_capture_queue(
    path: Path, pending: List[Dict[str, Any]], newly_parked: List[Dict[str, Any]]
) -> None:
    """Rewrite a pushed session's capture queue after an upload pass."""
    marker = read_pushed(path)
    if not marker:
        return
    marker["captures_pending"] = pending
    marker["captures_parked"] = list(marker.get("captures_parked") or []) + newly_parked
    _write_json(_sidecar(path, PUSHED_SUFFIX), marker)


def summarize(log_dir: Path) -> Dict[str, int]:
    """Count pushed / parked / pending sessions for ``status``."""
    files = session_files(log_dir)
    pushed = sum(1 for p in files if is_pushed(p))
    parked = sum(1 for p in files if is_parked(p))
    pending = sum(1 for p in files if not is_pushed(p) and not is_parked(p))
    captures_pending = sum(len(pending_captures(p)) for p in files if is_pushed(p))
    return {
        "total": len(files),
        "pushed": pushed,
        "parked": parked,
        "pending": pending,
        "captures_pending": captures_pending,
    }


def retention_guard(log_dir: Path, *, raw_uploads: bool) -> Callable[[Path], bool]:
    """Predicate for log retention: True for files the uploader still needs.

    Keeps sessions not yet pushed or parked, pushed sessions whose capture
    queue is non-empty, and (with raw uploads) queued capture dumps. Dumps of
    sessions not yet pushed are only listed once their session is filtered, so
    every dump at least as new as the oldest unfinished session is kept too.
    """
    sessions = session_files(log_dir)
    unfinished = {p for p in sessions if not is_pushed(p) and not is_parked(p)}
    queued = {
        Path(capture["path"])
        for p in sessions
        if is_pushed(p)
        for capture in pending_captures(p)
        if capture.get("path")
    }
    oldest_unfinished = min((p.stat().st_mtime for p in unfinished), default=None)

    def guard(path: Path) -> bool:
        if path.name.startswith("session_") and path.suffix == ".jsonl":
            return path in unfinished or bool(pending_captures(path))
        if not raw_uploads or path.suffix != ".l3dump":
            return False
        if path in queued:
            return True
        return oldest_unfinished is not None and path.stat().st_mtime >= oldest_unfinished

    return guard
