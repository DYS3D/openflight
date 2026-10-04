"""Copy session logs from the Pi's /api/session-logs, and optionally move them off it."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .archive import archive_session
from .store import Store

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-OpenFlight-Token"
TIMEOUT_S = 15
DOWNLOAD_TIMEOUT_S = 120


@dataclass
class PiSource:
    base_url: str
    token: str | None = None

    def _request(self, path: str, method: str = "GET", body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header(TOKEN_HEADER, self.token)
        return urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S)

    def _get_json(self, path: str):
        with self._request(path) as response:
            return json.loads(response.read())

    @staticmethod
    def _log_path(name: str) -> str:
        return "/api/session-logs/" + urllib.parse.quote(name)

    def listing(self) -> dict:
        return self._get_json("/api/session-logs")

    def read_log(self, name: str) -> str:
        with self._request(self._log_path(name)) as response:
            return response.read().decode("utf-8")

    def manifest(self, name: str) -> dict:
        return self._get_json(self._log_path(name) + "/manifest")

    def download_log(self, name: str, dest: Path) -> None:
        self._download(self._log_path(name), dest)

    def download_capture(self, name: str, relative: str, dest: Path) -> None:
        self._download(f"{self._log_path(name)}/files/{urllib.parse.quote(relative)}", dest)

    def _download(self, path: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._request(path) as response, dest.open("wb") as handle:
            shutil.copyfileobj(response, handle, 1 << 20)

    def delete(self, name: str, sha256: str, sizes: dict[str, int]) -> None:
        with self._request(self._log_path(name), "DELETE", {"sha256": sha256, "files": sizes}):
            pass


def sync_once(store: Store, source: PiSource, raw_dir: Path | None = None) -> dict:
    """Fetch every log whose size or mtime changed; with ``raw_dir``, also move
    finished sessions (raw data and captures) off the Pi.

    Records the outcome in the store.
    """
    now = datetime.now(UTC).isoformat()
    archived = 0
    try:
        listing = source.listing()
        sessions = listing["sessions"]
        updated = 0
        for entry in sessions:
            name, size, mtime = entry["name"], int(entry["size"]), float(entry["mtime"])
            if store.known_file(name) != (size, mtime):
                store.ingest(name, source.read_log(name), size, mtime)
                updated += 1
            if raw_dir is not None and listing.get("offload") and not entry.get("active"):
                archive_session(store, source, raw_dir, name)
                archived += 1
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Sync from %s failed: %s", source.base_url, exc)
        store.set_state("last_error", f"{now} {exc}")
        return {"ok": False, "error": str(exc), "archived": archived}
    if raw_dir is not None and not listing.get("offload"):
        message = "The Pi is not running with --session-log-offload; nothing was moved."
        store.set_state("last_error", f"{now} {message}")
        return {"ok": False, "error": message, "updated": updated, "archived": 0}
    store.set_state("last_sync", now)
    store.set_state("last_error", None)
    return {"ok": True, "updated": updated, "available": len(sessions), "archived": archived}


class SyncLoop:
    """Background sync every ``interval_s``; ``trigger()`` runs one now."""

    def __init__(
        self, store: Store, source: PiSource, interval_s: float, raw_dir: Path | None = None
    ):
        self._store = store
        self._source = source
        self._interval_s = interval_s
        self._raw_dir = raw_dir
        self._run_lock = threading.Lock()

    @property
    def offloading(self) -> bool:
        return self._raw_dir is not None

    def start(self) -> None:
        threading.Thread(target=self._loop, name="pi-sync", daemon=True).start()

    def trigger(self) -> dict:
        with self._run_lock:
            return sync_once(self._store, self._source, self._raw_dir)

    def _loop(self) -> None:
        while True:
            self.trigger()
            time.sleep(self._interval_s)
