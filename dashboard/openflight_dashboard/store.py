"""SQLite copy of every session, kept even after the Pi prunes its own logs."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import astuple
from pathlib import Path

from .logs import Shot, parse_session_log

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_files (
    name TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    location TEXT
);
CREATE TABLE IF NOT EXISTS shots (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    shot_number INTEGER,
    club TEXT NOT NULL,
    profile_id TEXT,
    profile_name TEXT,
    ball_speed REAL NOT NULL,
    club_speed REAL,
    smash REAL,
    carry REAL,
    launch_v REAL,
    launch_h REAL,
    spin REAL,
    spin_axis REAL,
    PRIMARY KEY (session_id, timestamp)
);
CREATE TABLE IF NOT EXISTS archived_sessions (
    name TEXT PRIMARY KEY,
    folder TEXT NOT NULL,
    archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS hidden_sessions (
    id TEXT PRIMARY KEY,
    hidden_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

SHOT_COLUMNS = [name for name in Shot.__dataclass_fields__]


class Store:
    """One connection shared across threads, serialised by a lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def known_file(self, name: str) -> tuple[int, float] | None:
        rows = self.query("SELECT size, mtime FROM log_files WHERE name = ?", (name,))
        return (rows[0]["size"], rows[0]["mtime"]) if rows else None

    def ingest(self, name: str, text: str, size: int, mtime: float) -> int:
        """Replace one session with the log's current contents; returns its shot count."""
        session = parse_session_log(name, text)
        if self.query("SELECT 1 FROM hidden_sessions WHERE id = ?", (session.session_id,)):
            # Hidden sessions stay out of the dashboard; remember the file so
            # it is not fetched again.
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO log_files (name, size, mtime) VALUES (?, ?, ?)",
                    (name, size, mtime),
                )
            return 0
        placeholders = ", ".join("?" for _ in SHOT_COLUMNS)
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session.session_id,))
            self._conn.execute(
                "INSERT INTO sessions (id, started_at, location) VALUES (?, ?, ?)",
                (session.session_id, session.started_at, session.location),
            )
            self._conn.executemany(
                f"INSERT INTO shots (session_id, {', '.join(SHOT_COLUMNS)}) "
                f"VALUES (?, {placeholders})",
                [(session.session_id, *astuple(shot)) for shot in session.shots],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO log_files (name, size, mtime) VALUES (?, ?, ?)",
                (name, size, mtime),
            )
        return len(session.shots)

    def session_ids(self) -> list[str]:
        """Visible sessions, newest first."""
        rows = self.query("SELECT id FROM sessions ORDER BY started_at DESC, id DESC")
        return [row["id"] for row in rows]

    def profile_id_for_name(self, name: str) -> str | None:
        """The profile id already used for this golfer's name, ignoring case."""
        rows = self.query(
            "SELECT profile_id FROM shots WHERE profile_id IS NOT NULL "
            "AND lower(profile_name) = lower(?) GROUP BY profile_id ORDER BY COUNT(*) DESC",
            (name,),
        )
        return rows[0]["profile_id"] if rows else None

    def hide(self, session_ids: list[str]) -> int:
        """Take sessions off the dashboard for good. Raw files on disk are kept."""
        with self._lock, self._conn:
            for session_id in session_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO hidden_sessions (id) VALUES (?)", (session_id,)
                )
                self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return len(session_ids)

    def mark_archived(self, name: str, folder: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO archived_sessions (name, folder) VALUES (?, ?)",
                (name, folder),
            )

    def archived_count(self) -> int:
        return self.query("SELECT COUNT(*) AS n FROM archived_sessions")[0]["n"]

    def get_state(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM sync_state WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, value)
            )
