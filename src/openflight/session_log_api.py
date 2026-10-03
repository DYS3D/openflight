"""Read-only HTTP access to session logs for a home dashboard (``--session-log-api``).

Off by default. When on, ``GET /api/session-logs`` lists the session JSONL
files in the log directory and ``GET /api/session-logs/<name>`` returns one.
Only ``session_*.jsonl`` files directly inside the log directory are served;
raw radar logs, camera captures and anything else stay on the Pi. The routes
sit behind the same access-control token as the rest of the server.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Callable

from flask import Blueprint, abort, jsonify, send_file

SESSION_LOG_NAME = re.compile(r"^session_[A-Za-z0-9_-]+\.jsonl$")


def add_session_log_api_args(parser: argparse.ArgumentParser) -> None:
    """Server flag for the dashboard log API (off by default)."""
    parser.add_argument(
        "--session-log-api",
        action="store_true",
        help=(
            "Serve session logs read-only at /api/session-logs so a home dashboard "
            "(see dashboard/) can copy them. Off by default."
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


def list_session_logs(log_dir: Path) -> list[dict]:
    """Name, size and modification time of every session log, oldest first."""
    if not log_dir.is_dir():
        return []
    entries = []
    for path in log_dir.iterdir():
        if SESSION_LOG_NAME.match(path.name) and path.is_file():
            stat = path.stat()
            entries.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return sorted(entries, key=lambda entry: (entry["mtime"], entry["name"]))


def create_session_log_blueprint(get_log_dir: Callable[[], Path]) -> Blueprint:
    """Blueprint with the two read-only routes, reading ``get_log_dir()`` per request."""
    blueprint = Blueprint("session_log_api", __name__)

    @blueprint.route("/api/session-logs")
    def session_logs():
        return jsonify({"sessions": list_session_logs(get_log_dir())})

    @blueprint.route("/api/session-logs/<name>")
    def session_log(name: str):
        path = resolve_session_log(get_log_dir(), name)
        if path is None:
            abort(404)
        return send_file(path, mimetype="application/x-ndjson", max_age=0)

    return blueprint
