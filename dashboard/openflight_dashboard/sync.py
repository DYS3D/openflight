"""Copy new or grown session logs from the Pi's /api/session-logs."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

from .store import Store

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-OpenFlight-Token"
TIMEOUT_S = 15


@dataclass
class PiSource:
    base_url: str
    token: str | None = None

    def _get(self, path: str) -> bytes:
        request = urllib.request.Request(self.base_url.rstrip("/") + path)
        if self.token:
            request.add_header(TOKEN_HEADER, self.token)
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()

    def list_logs(self) -> list[dict]:
        return json.loads(self._get("/api/session-logs"))["sessions"]

    def read_log(self, name: str) -> str:
        return self._get("/api/session-logs/" + urllib.parse.quote(name)).decode("utf-8")


def sync_once(store: Store, source: PiSource) -> dict:
    """Fetch every log whose size or mtime changed. Records the outcome in the store."""
    now = datetime.now(UTC).isoformat()
    try:
        listing = source.list_logs()
        updated = 0
        for entry in listing:
            name, size, mtime = entry["name"], int(entry["size"]), float(entry["mtime"])
            if store.known_file(name) == (size, mtime):
                continue
            store.ingest(name, source.read_log(name), size, mtime)
            updated += 1
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Sync from %s failed: %s", source.base_url, exc)
        store.set_state("last_error", f"{now} {exc}")
        return {"ok": False, "error": str(exc)}
    store.set_state("last_sync", now)
    store.set_state("last_error", None)
    return {"ok": True, "updated": updated, "available": len(listing)}


class SyncLoop:
    """Background sync every ``interval_s``; ``trigger()`` runs one now."""

    def __init__(self, store: Store, source: PiSource, interval_s: float):
        self._store = store
        self._source = source
        self._interval_s = interval_s
        self._run_lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="pi-sync", daemon=True).start()

    def trigger(self) -> dict:
        with self._run_lock:
            return sync_once(self._store, self._source)

    def _loop(self) -> None:
        while True:
            self.trigger()
            time.sleep(self._interval_s)
