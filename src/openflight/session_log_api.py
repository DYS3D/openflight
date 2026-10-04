"""HTTP access to session logs for a home dashboard.

``--session-log-api`` (off by default) lists the session JSONL files in the log
directory at ``GET /api/session-logs`` and returns one at
``GET /api/session-logs/<name>``. Only ``session_*.jsonl`` files directly inside
the log directory are served.

``--session-log-offload`` (off by default, needs ``--session-log-api``) lets the
dashboard move finished sessions off the Pi: ``GET .../<name>/manifest`` names
the camera and IWR6843 captures the session references, ``GET .../<name>/files/
<path>`` returns one of them, and ``DELETE .../<name>`` removes the session and
its captures once the caller proves it holds an identical copy. The session
being recorded is never offered for deletion.

The routes sit behind the same access-control token as the rest of the server.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

from flask import Blueprint, abort, jsonify, request, send_file

SESSION_LOG_NAME = re.compile(r"^session_[A-Za-z0-9_-]+\.jsonl$")
# Session entries that point at a capture written next to the log.
CAPTURE_ENTRY_TYPES = ("camera_capture", "iwr6843_capture")
SIDECAR_SUFFIXES = (".pushed", ".parked", ".state")
CAMERA_SHOT_PREFIX = "camera_"


def add_session_log_api_args(parser: argparse.ArgumentParser) -> None:
    """Server flags for the dashboard log API (both off by default)."""
    parser.add_argument(
        "--session-log-api",
        action="store_true",
        help=(
            "Serve session logs read-only at /api/session-logs so a home dashboard "
            "(see dashboard/) can copy them. Off by default."
        ),
    )
    parser.add_argument(
        "--session-log-offload",
        action="store_true",
        help=(
            "With --session-log-api, also let the dashboard download each finished "
            "session's captures and then delete the session from the Pi once it "
            "holds a verified copy. Off by default."
        ),
    )


def resolve_session_log(log_dir: Path, name: str) -> Path | None:
    """The session log called ``name`` in ``log_dir``, or None if not servable."""
    if not SESSION_LOG_NAME.match(name):
        return None
    path = (log_dir / name).resolve()
    if path.parent != log_dir.resolve() or not path.is_file():
        return None
    return path


def list_session_logs(log_dir: Path, active_name: Optional[str] = None) -> list[dict]:
    """Name, size, modification time and whether it is still being written, oldest first."""
    if not log_dir.is_dir():
        return []
    entries = []
    for path in log_dir.iterdir():
        if SESSION_LOG_NAME.match(path.name) and path.is_file():
            stat = path.stat()
            entries.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "active": path.name == active_name,
                }
            )
    return sorted(entries, key=lambda entry: (entry["mtime"], entry["name"]))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def session_capture_files(log_dir: Path, session_path: Path) -> dict[str, Path]:
    """Capture files the session references, keyed by path relative to ``log_dir``.

    A capture path may be one file (an IWR6843 dump) or a directory (a camera
    shot). Anything outside ``log_dir`` is ignored rather than served.
    """
    root = log_dir.resolve()
    files: dict[str, Path] = {}
    raw_log = root / f"radar_raw_{session_path.stem.removeprefix('session_').rsplit('_', 1)[0]}.log"
    if raw_log.is_file():
        files[raw_log.name] = raw_log
    with session_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in CAPTURE_ENTRY_TYPES:
                continue
            capture = entry.get("capture_path")
            if not isinstance(capture, str) or not capture:
                continue
            target = Path(capture).resolve()
            if not target.is_relative_to(root):
                continue
            candidates = sorted(target.rglob("*")) if target.is_dir() else [target]
            for candidate in candidates:
                if candidate.is_file() and not candidate.is_symlink():
                    files[candidate.relative_to(root).as_posix()] = candidate
    return files


def session_manifest(log_dir: Path, session_path: Path) -> dict:
    """What a copy must match before the Pi deletes the session: checksum and capture sizes."""
    files = session_capture_files(log_dir, session_path)
    return {
        "name": session_path.name,
        "size": session_path.stat().st_size,
        "sha256": _sha256(session_path),
        "files": [
            {"path": relative, "size": path.stat().st_size}
            for relative, path in sorted(files.items())
        ],
    }


def delete_session(log_dir: Path, session_path: Path, sha256: str, sizes: dict) -> int:
    """Remove a session and its captures if they match what the caller copied.

    Raises ValueError (and deletes nothing) when the log's checksum or any
    capture's size differs from the caller's copy.
    """
    files = session_capture_files(log_dir, session_path)
    if sha256 != _sha256(session_path):
        raise ValueError("session log changed since it was copied")
    for relative, path in files.items():
        if sizes.get(relative) != path.stat().st_size:
            raise ValueError(f"{relative} was not copied completely")

    sidecars = [session_path.with_name(session_path.name + suffix) for suffix in SIDECAR_SUFFIXES]
    removed = 0
    for path in [*files.values(), *(p for p in sidecars if p.is_file()), session_path]:
        path.unlink(missing_ok=True)
        removed += 1
    # Only per-shot camera folders go; the iwr6843/ and camera/ folders the
    # running server writes into must stay.
    root = log_dir.resolve()
    for directory in {p.parent for p in files.values()}:
        if directory.name.startswith(CAMERA_SHOT_PREFIX) and directory.parent.name == "camera":
            if directory.is_relative_to(root):
                try:
                    directory.rmdir()
                except OSError:
                    pass  # holds files this session does not reference
    return removed


def create_session_log_blueprint(
    get_log_dir: Callable[[], Path],
    get_active_name: Callable[[], Optional[str]] = lambda: None,
    allow_offload: bool = False,
) -> Blueprint:
    """Blueprint with the log routes, reading ``get_log_dir()`` per request."""
    blueprint = Blueprint("session_log_api", __name__)

    def finished_session(name: str) -> Path:
        if not allow_offload:
            abort(404)
        path = resolve_session_log(get_log_dir(), name)
        if path is None:
            abort(404)
        if name == get_active_name():
            abort(409, "Session is still being recorded")
        return path

    @blueprint.route("/api/session-logs")
    def session_logs():
        return jsonify(
            {
                "sessions": list_session_logs(get_log_dir(), get_active_name()),
                "offload": allow_offload,
            }
        )

    @blueprint.route("/api/session-logs/<name>")
    def session_log(name: str):
        path = resolve_session_log(get_log_dir(), name)
        if path is None:
            abort(404)
        return send_file(path, mimetype="application/x-ndjson", max_age=0)

    @blueprint.route("/api/session-logs/<name>/manifest")
    def manifest(name: str):
        return jsonify(session_manifest(get_log_dir(), finished_session(name)))

    @blueprint.route("/api/session-logs/<name>/files/<path:relative>")
    def capture_file(name: str, relative: str):
        files = session_capture_files(get_log_dir(), finished_session(name))
        if relative not in files:
            abort(404)
        return send_file(files[relative], mimetype="application/octet-stream", max_age=0)

    @blueprint.route("/api/session-logs/<name>", methods=["DELETE"])
    def remove(name: str):
        path = finished_session(name)
        body = request.get_json(silent=True) or {}
        sha256 = body.get("sha256")
        sizes = body.get("files")
        if not isinstance(sha256, str) or not isinstance(sizes, dict):
            abort(400, "Send the copy's sha256 and file sizes")
        try:
            removed = delete_session(get_log_dir(), path, sha256, sizes)
        except ValueError as error:
            return jsonify({"error": str(error)}), 412
        return jsonify({"deleted": removed})

    return blueprint
