"""Server side of ``--update-check``: periodic checks, per-client status, apply + restart.

Only the kiosk (a loopback client, i.e. the Pi's own touchscreen) may check
or apply. Phones and TVs see the status read-only, whatever ``--auth-required``
says, so nothing on the network can make the Pi run ``git``/``uv``/``npm``.

Applying stops the hardware, runs :meth:`Updater.apply`, then exits with
:data:`UPDATE_EXIT_CODE`. ``openflight.service`` has ``Restart=on-failure``,
so systemd starts the new version a few seconds later; a failed update has
already been rolled back and restarts the old version the same way. The
outcome is saved to :data:`DEFAULT_RESULT_FILE` so the restarted server can
show it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .updater import (
    STATE_AVAILABLE,
    STATE_CHECKING,
    STATE_FAILED,
    STATE_RESTARTING,
    STATE_UPDATING,
    ApplyResult,
    UpdateError,
    Updater,
    short_sha,
)

logger = logging.getLogger(__name__)

# EX_TEMPFAIL: non-zero so systemd's Restart=on-failure brings the service back.
UPDATE_EXIT_CODE = 75
# Refuse to start while a shot was being captured or processed this recently.
UPDATE_IDLE_REQUIRED_S = 15.0
FIRST_CHECK_DELAY_S = 30.0
# Time for the final status to reach the screen before the process exits.
RESTART_DELAY_S = 3.0
DEFAULT_RESULT_FILE = Path.home() / ".local" / "state" / "openflight" / "last_update.json"

KIOSK_ONLY = "Updates can only be started from the OpenFlight touchscreen"

Emit = Callable[[str, dict, Optional[str]], None]


def restart_mode_from_env(environ=os.environ) -> str:
    """``systemd`` when the service manager will restart us, else ``manual``."""
    return "systemd" if environ.get("INVOCATION_ID") else "manual"


class UpdateService:
    """Owns the check loop and the apply flow for one server process."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        updater: Updater,
        *,
        emit: Emit,
        stop_hardware: Callable[[], object],
        exit_process: Callable[[int], None],
        seconds_since_activity: Callable[[], float] = lambda: math.inf,
        check_interval_s: float = 6 * 3600.0,
        first_check_delay_s: float = FIRST_CHECK_DELAY_S,
        restart_delay_s: float = RESTART_DELAY_S,
        idle_required_s: float = UPDATE_IDLE_REQUIRED_S,
        restart_mode: str = "systemd",
        result_file: Optional[Path] = DEFAULT_RESULT_FILE,
        start_thread: Callable[[Callable[[], None]], None] | None = None,
    ):
        self.updater = updater
        self._emit = emit
        self._stop_hardware = stop_hardware
        self._exit_process = exit_process
        self._seconds_since_activity = seconds_since_activity
        self._check_interval_s = max(60.0, check_interval_s)
        self._first_check_delay_s = first_check_delay_s
        self._restart_delay_s = restart_delay_s
        self._idle_required_s = idle_required_s
        self.restart_mode = restart_mode
        self._result_file = result_file
        self._start_thread = start_thread or _daemon_thread
        self._clients: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._checking = False
        self._applying = False
        self.last_result = self._load_result()

    # -- status -----------------------------------------------------------

    def payload(self, can_apply: bool) -> dict:
        """The ``update_status`` payload for a client with or without apply rights."""
        data = self.updater.status.to_dict()
        data["can_apply"] = bool(can_apply)
        data["restart"] = self.restart_mode
        data["last_result"] = self.last_result
        return data

    def payload_for(self, sid: Optional[str], is_kiosk: bool) -> dict:
        """Payload for one socket, using the rights recorded when it connected."""
        with self._lock:
            kiosk = self._clients.get(sid, is_kiosk) if sid else is_kiosk
        return self.payload(kiosk)

    def publish(self) -> None:
        """Send every connected client its own copy of the status."""
        with self._lock:
            clients = list(self._clients.items())
        for sid, kiosk in clients:
            self._emit("update_status", self.payload(kiosk), sid)

    def client_connected(self, sid: Optional[str], is_kiosk: bool) -> None:
        """Remember the client's rights and send it the current status."""
        if sid:
            with self._lock:
                self._clients[sid] = is_kiosk
        self._emit("update_status", self.payload(is_kiosk), sid)

    def client_disconnected(self, sid: Optional[str]) -> None:
        """Forget a client."""
        with self._lock:
            self._clients.pop(sid, None)

    # -- checking ---------------------------------------------------------

    def start(self) -> None:
        """Start the background check loop."""
        self._start_thread(self._check_loop)

    def stop(self) -> None:
        """Stop the check loop after its current wait."""
        self._stop.set()

    def _check_loop(self) -> None:
        if self._stop.wait(self._first_check_delay_s):
            return
        while not self._stop.is_set():
            if not self._applying:
                self._run_check()
            if self._stop.wait(self._check_interval_s):
                return

    def _run_check(self) -> None:
        with self._lock:
            if self._checking or self._applying:
                return
            self._checking = True
        try:
            self.updater.status.state = STATE_CHECKING
            self.publish()
            status = self.updater.check()
            if status.state == STATE_AVAILABLE:
                logger.info(
                    "[UPDATE] %d new commit(s) on %s/%s (%s -> %s)",
                    status.behind,
                    status.remote,
                    status.branch,
                    status.current,
                    status.latest,
                )
        except Exception:  # pylint: disable=broad-except
            logger.exception("[UPDATE] Update check crashed")
        finally:
            with self._lock:
                self._checking = False
            self.publish()

    def request_check(self, is_kiosk: bool) -> Optional[str]:
        """Start a check now. Returns why it was refused, or None."""
        if not is_kiosk:
            return KIOSK_ONLY
        with self._lock:
            if self._applying:
                return "An update is already being installed"
            if self._checking:
                return None
        self._start_thread(self._run_check)
        return None

    # -- applying ---------------------------------------------------------

    def request_apply(self, is_kiosk: bool) -> Optional[str]:
        """Start installing the available update. Returns why it was refused, or None."""
        if not is_kiosk:
            return KIOSK_ONLY
        idle_for = self._seconds_since_activity()
        if idle_for < self._idle_required_s:
            return "A shot is being processed; try again in a few seconds"
        with self._lock:
            if self._applying:
                return "An update is already being installed"
            if self._checking:
                return "An update check is running; try again in a moment"
            refusal = self.updater.preflight_error()
            if refusal:
                return refusal
            self._applying = True
        self.updater.status.state = STATE_UPDATING
        self.updater.status.step = "Stopping the radar"
        self.publish()
        self._start_thread(self._apply_flow)
        return None

    def _on_step(self, step: str) -> None:
        logger.info("[UPDATE] %s", step)
        self.updater.status.step = step
        self.publish()

    def _apply_flow(self) -> None:
        status = self.updater.status
        previous = status.current
        logger.info("[UPDATE] Installing %s/%s from the touchscreen", status.remote, status.branch)
        try:
            self._stop_hardware()
        except Exception:  # pylint: disable=broad-except
            logger.exception("[UPDATE] Stopping hardware failed; continuing with the update")
        try:
            result = self.updater.apply(self._on_step)
        except UpdateError as error:
            result = ApplyResult(False, None, None, str(error), error.detail, rolled_back=True)
        except Exception as error:  # pylint: disable=broad-except
            logger.exception("[UPDATE] Update crashed")
            result = ApplyResult(False, None, None, f"Update crashed: {error}")

        status.step = None
        if result.ok:
            status.state = STATE_RESTARTING
            logger.info(
                "[UPDATE] Installed %s (was %s); restarting",
                short_sha(result.installed),
                previous,
            )
        else:
            status.state = STATE_FAILED
            status.error = result.error
            status.rolled_back = result.rolled_back
            logger.error(
                "[UPDATE] Update failed: %s%s. %s",
                result.error,
                f"\n{result.detail}" if result.detail else "",
                "Rolled back." if result.rolled_back else "ROLLBACK INCOMPLETE.",
            )
        self.last_result = self._save_result(result)
        self.publish()
        time.sleep(self._restart_delay_s)
        self._exit_process(UPDATE_EXIT_CODE)

    # -- result file ------------------------------------------------------

    def _save_result(self, result: ApplyResult) -> dict:
        record = {
            "ok": result.ok,
            "previous": short_sha(result.previous),
            "installed": short_sha(result.installed),
            "error": result.error,
            "rolled_back": result.rolled_back,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "log_path": str(result.log_path) if result.log_path else None,
        }
        if self._result_file:
            try:
                self._result_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._result_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(record), encoding="utf-8")
                tmp.replace(self._result_file)
            except OSError:
                logger.warning("[UPDATE] Could not save the update result", exc_info=True)
        return record

    def _load_result(self) -> Optional[dict]:
        if not self._result_file:
            return None
        try:
            data = json.loads(self._result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None


def _daemon_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="openflight-update", daemon=True).start()
