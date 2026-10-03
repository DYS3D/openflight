"""GPIO-triggered IWR6843 L3 capture and OPS-shot correlation."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import serial

from openflight.gpio_factory import ensure_lgpio_pin_factory
from openflight.iwr6843.driver import DumpRestartError, IWR6843Radar
from openflight.iwr6843.dump import HEADER, parse_header, payload_nbytes
from openflight.radar_reconnect import (
    RADAR_STATE_CONNECTED,
    RADAR_STATE_RECONNECTING,
    reconnect_backoff_s,
)

logger = logging.getLogger(__name__)

_GRACEFUL_DUMP_SHUTDOWN_S = 12.0
# Each capture holds a ~768 KiB dump. Unclaimed ones (false triggers, shots
# whose OPS side never arrived) must not accumulate for a whole session. The
# age limit must exceed how long a real shot can legitimately wait: the
# server's enrichment queue (two waiting shots, 20 s deadline each) can hold a
# valid capture for ~40 s before capture_for_shot() asks for it.
_MAX_PENDING_CAPTURES = 4
_MAX_PENDING_CAPTURE_AGE_S = 60.0


def tx_order_from_config(config_path: str | Path) -> str:
    """Infer the vertical physical TX order from chirp masks in a cfg."""
    masks = []
    with Path(config_path).open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("chirpCfg"):
                masks.append(line.rsplit(maxsplit=1)[-1])
    if masks == ["1", "4"]:
        return "normal"
    if masks == ["4", "1"]:
        return "reversed"
    if masks == ["1", "2", "4"]:
        return "normal"
    raise ValueError(f"IWR6843 config must contain chirp TX masks 1/4, 4/1, or 1/2/4, got {masks}")


@dataclass(frozen=True)
class IWR6843Capture:
    """One GPIO edge and its completed L3 dump."""

    sequence: int
    trigger_timestamp: float
    completed_timestamp: float
    dump_duration_s: float
    raw: bytes | None
    path: Path | None
    error: str | None = None
    temperature_report: dict[str, int] | None = None

    @property
    def valid(self) -> bool:
        """Whether a complete dump was captured."""
        return self.raw is not None and self.error is None


class IWR6843CaptureMonitor:
    """Capture TI rolling-buffer dumps on the same sound edge used by OPS.

    The GPIO callback only timestamps and queues the edge. Serial transfer is
    handled on a dedicated thread because one 768 KiB dump takes several
    seconds at the firmware UART rate.
    """

    def __init__(
        self,
        *,
        config_path: str | Path,
        output_dir: str | Path,
        port: str | None = None,
        gpio_pin: int = 17,
        radar: IWR6843Radar | None = None,
        button_factory: Callable | None = None,
        match_tolerance_s: float = 0.75,
        save_dumps: bool = False,
        trigger_observers: list[Callable[[float], None]] | None = None,
        radar_auto_reconnect: bool = False,
        radar_status_callback: Callable[[str], None] | None = None,
        radar_factory: Callable[[str | None], IWR6843Radar] = IWR6843Radar,
    ):
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir).expanduser()
        self.gpio_pin = gpio_pin
        self.match_tolerance_s = match_tolerance_s
        self.save_dumps = save_dumps
        self.radar = radar or radar_factory(port)
        self.radar_auto_reconnect = radar_auto_reconnect
        self.radar_state = RADAR_STATE_CONNECTED
        self._configured_port = port
        self._radar_factory = radar_factory
        self._radar_status_callback = radar_status_callback
        self._button_factory = button_factory
        self._button = None
        self._running = False
        self._armed = False
        self._capture_active = False
        self._sequence = 0
        self._last_edge_timestamp = 0.0
        self._events: queue.Queue[float | None] = queue.Queue(maxsize=1)
        self._captures: deque[IWR6843Capture] = deque(maxlen=_MAX_PENDING_CAPTURES)
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._trigger_observers = list(trigger_observers or [])

    @property
    def port(self) -> str:
        """Connected TI serial port."""
        return self.radar.port

    def start(self, *, armed: bool = True) -> None:
        """Configure the radar and GPIO, optionally arming trigger capture."""
        if self._running:
            return
        if not self.config_path.is_file():
            raise FileNotFoundError(f"IWR6843 config not found: {self.config_path}")
        if self.save_dumps:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        configured = False
        try:
            self.radar.send_config(str(self.config_path))
            configured = True

            button_factory = self._button_factory
            if button_factory is None:
                # Must precede the first gpiozero device: on a Pi 5 gpiozero's
                # own auto-detection fails outright. See gpio_factory.
                ensure_lgpio_pin_factory()

                from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel

                button_factory = Button
            # No gpiozero debounce: lgpio delays delivery by the debounce interval,
            # which previously cost the first 50 ms of ball flight.
            self._button = button_factory(self.gpio_pin, pull_up=False, bounce_time=None)
            self._stop_event.clear()
            self._running = True
            self._worker = threading.Thread(
                target=self._capture_loop,
                name="iwr6843-capture",
                daemon=True,
            )
            self._worker.start()
            if armed:
                self.arm()
        except Exception:
            self._running = False
            if self._button is not None:
                self._button.close()
                self._button = None
            if configured:
                self._stop_sensor_and_close()
            else:
                self.radar.close()
            raise
        logger.info(
            "[IWR6843] Configured on BCM%d using %s (%s%s)",
            self.gpio_pin,
            self.port,
            self.config_path.name,
            ", armed" if self._armed else ", waiting for OPS",
        )

    def arm(self) -> None:
        """Accept GPIO edges after the OPS trigger path is fully initialized."""
        if not self._running:
            raise RuntimeError("cannot arm an IWR6843 monitor that is not running")
        if self._armed:
            return
        # Attach while logically disarmed so a line already high from OPS
        # startup cannot synchronously create a false capture.
        self._button.when_pressed = self.notify_trigger
        self._armed = True
        logger.info("[IWR6843] Armed on BCM%d", self.gpio_pin)

    def add_trigger_observer(self, observer: Callable[[float], None]) -> None:
        """Call ``observer(edge_timestamp)`` for every accepted trigger edge."""
        self._trigger_observers.append(observer)

    def notify_trigger(self, timestamp: float | None = None) -> bool:
        """Queue a GPIO edge without doing serial work in the callback."""
        if not self._running or not self._armed:
            return False
        edge_timestamp = time.time() if timestamp is None else float(timestamp)
        with self._condition:
            if self.radar_state == RADAR_STATE_RECONNECTING:
                logger.debug("[IWR6843] Ignoring trigger edge while reconnecting")
                return False
            # Reject acoustic ringing and any second edge while the seven-second
            # UART dump is in flight. The OPS side makes the same shot wait.
            if (
                self._capture_active
                or not self._events.empty()
                or edge_timestamp - self._last_edge_timestamp < 0.1
            ):
                logger.debug("[IWR6843] Ignoring duplicate/busy trigger edge")
                return False
            self._last_edge_timestamp = edge_timestamp
            self._events.put_nowait(edge_timestamp)
            self._condition.notify_all()
        for observer in self._trigger_observers:
            try:
                observer(edge_timestamp)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Trigger observer failed", exc_info=True)
        return True

    def _validate_dump(self, raw: bytes) -> dict:
        if len(raw) < HEADER.size:
            raise ValueError(f"short IWR6843 dump: {len(raw)} bytes")
        metadata = parse_header(raw)
        expected = metadata["header_nbytes"] + payload_nbytes(metadata, raw)
        if len(raw) != expected:
            raise ValueError(f"short IWR6843 dump: {len(raw)} bytes, expected {expected}")
        return metadata

    def _capture_path(self, sequence: int, trigger_timestamp: float) -> Path:
        timestamp = datetime.fromtimestamp(trigger_timestamp).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self.output_dir / f"iwr6843_{timestamp}_{sequence:03d}.l3dump"

    def _capture_loop(self) -> None:
        while self._running:
            edge_timestamp = self._events.get()
            if edge_timestamp is None or not self._running:
                break
            with self._condition:
                self._capture_active = True
                self._sequence += 1
                sequence = self._sequence
            start = time.time()
            raw = None
            path = None
            error = None
            metadata = None
            link_error: Exception | None = None
            restart_error: DumpRestartError | None = None
            try:
                logger.info(
                    "[IWR6843] Trigger #%d: dumping firmware-frozen L3 ring",
                    sequence,
                )
                raw = self.radar.read_dump()
                metadata = self._validate_dump(raw)
                if self.save_dumps:
                    path = self._capture_path(sequence, edge_timestamp)
                    path.write_bytes(raw)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error = str(exc)
                raw = None
                if self.radar_auto_reconnect and isinstance(exc, (serial.SerialException, OSError)):
                    link_error = exc
                elif isinstance(exc, DumpRestartError):
                    restart_error = exc
                else:
                    logger.warning("[IWR6843] Capture #%d failed: %s", sequence, exc, exc_info=True)
            completed = time.time()
            capture = IWR6843Capture(
                sequence=sequence,
                trigger_timestamp=edge_timestamp,
                completed_timestamp=completed,
                dump_duration_s=completed - start,
                raw=raw,
                path=path,
                error=error,
                temperature_report=(
                    metadata.get("temperature_report") if metadata is not None else None
                ),
            )
            with self._condition:
                self._capture_active = False
                self._discard_expired_captures()
                if len(self._captures) == self._captures.maxlen:
                    logger.warning(
                        "[IWR6843] Discarding unclaimed capture #%d: pending queue full",
                        self._captures[0].sequence,
                    )
                self._captures.append(capture)
                self._condition.notify_all()
            logger.info(
                "[IWR6843] Capture #%d complete: %s in %.2fs",
                sequence,
                f"{len(raw)} bytes" if raw is not None else error,
                capture.dump_duration_s,
            )
            if link_error is not None:
                self._reconnect_radar(link_error)
            elif restart_error is not None:
                self._restart_capture(restart_error)

    def _restart_capture(self, error: DumpRestartError) -> None:
        """Re-send the config so capture resumes after a failed firmware restart."""
        logger.warning("[IWR6843] %s; re-sending config", error)
        try:
            self.radar.send_config(str(self.config_path))
        except (serial.SerialException, OSError, RuntimeError) as exc:
            if self.radar_auto_reconnect:
                self._reconnect_radar(exc)
            else:
                logger.error("[IWR6843] Could not restart capture: %s", exc)
            return
        logger.info("[IWR6843] Capture restarted after firmware restart failure")

    def _set_radar_state(self, state: str) -> None:
        """Record the serial link state and report it without breaking capture."""
        self.radar_state = state
        if self._radar_status_callback is None:
            return
        try:
            self._radar_status_callback(state)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] Radar status callback failed", exc_info=True)

    def _reconnect_radar(self, error: Exception) -> None:
        """Close the dead port and re-detect the board with capped back-off.

        Logs once when the link is lost and once when it is back; individual
        failed attempts are debug-only. GPIO edges are ignored meanwhile.
        Returns once the config is re-sent or the monitor is stopped.
        """
        logger.error(
            "[IWR6843] Serial link lost on %s (%s); reconnecting",
            self.port,
            error,
        )
        self._set_radar_state(RADAR_STATE_RECONNECTING)
        self._close_quietly(self.radar)

        failed_attempts = 0
        started = time.monotonic()
        while self._running:
            try:
                # Auto-detect again (stable udev name first) unless a port
                # was pinned on the command line.
                radar = self._radar_factory(self._configured_port)
            except (serial.SerialException, OSError, RuntimeError) as exc:
                failed_attempts += 1
                self._wait_before_retry(failed_attempts, exc)
                continue
            try:
                radar.send_config(str(self.config_path))
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._close_quietly(radar)
                failed_attempts += 1
                self._wait_before_retry(failed_attempts, exc)
                continue
            if not self._running:
                # stop() may have given up joining this thread while send_config ran.
                self._close_quietly(radar)
                return
            self.radar = radar
            logger.info(
                "[IWR6843] Reconnected on %s after %d failed attempt(s) in %.0fs",
                self.port,
                failed_attempts,
                time.monotonic() - started,
            )
            self._set_radar_state(RADAR_STATE_CONNECTED)
            return

    def _wait_before_retry(self, failed_attempts: int, exc: Exception) -> None:
        delay = reconnect_backoff_s(failed_attempts)
        logger.debug(
            "[IWR6843] Reconnect attempt %d failed (%s); retry in %.0fs",
            failed_attempts,
            exc,
            delay,
        )
        self._stop_event.wait(delay)

    @staticmethod
    def _close_quietly(radar: IWR6843Radar) -> None:
        try:
            radar.close()
        except (serial.SerialException, OSError):
            logger.debug("[IWR6843] Ignoring close error on lost port", exc_info=True)

    def _discard_expired_captures(self) -> None:
        """Drop completed captures nobody claimed in time. Caller holds the lock."""
        cutoff = time.time() - _MAX_PENDING_CAPTURE_AGE_S
        while self._captures and self._captures[0].completed_timestamp < cutoff:
            expired = self._captures.popleft()
            logger.warning(
                "[IWR6843] Discarding capture #%d unclaimed for over %.0fs",
                expired.sequence,
                _MAX_PENDING_CAPTURE_AGE_S,
            )

    def capture_for_shot(
        self,
        impact_timestamp: float | None,
        *,
        timeout_s: float = 12.0,
    ) -> IWR6843Capture | None:
        """Consume the capture nearest an OPS impact timestamp."""
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                self._discard_expired_captures()
                if impact_timestamp is None and self._captures:
                    return self._captures.popleft()

                if impact_timestamp is not None:
                    cutoff = impact_timestamp - self.match_tolerance_s
                    while self._captures and self._captures[0].trigger_timestamp < cutoff:
                        stale = self._captures.popleft()
                        logger.warning(
                            "[IWR6843] Discarding unmatched capture #%d (edge %.3f, shot %.3f)",
                            stale.sequence,
                            stale.trigger_timestamp,
                            impact_timestamp,
                        )
                    matches = [
                        capture
                        for capture in self._captures
                        if abs(capture.trigger_timestamp - impact_timestamp)
                        <= self.match_tolerance_s
                    ]
                    if matches:
                        selected = min(
                            matches,
                            key=lambda capture: abs(capture.trigger_timestamp - impact_timestamp),
                        )
                        self._captures.remove(selected)
                        return selected

                    matching_capture_active = abs(
                        self._last_edge_timestamp - impact_timestamp
                    ) <= self.match_tolerance_s and (
                        self._capture_active or not self._events.empty()
                    )
                    if (
                        time.time() > impact_timestamp + self.match_tolerance_s
                        and not matching_capture_active
                    ):
                        return None

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def stop(self) -> None:
        """Drain active capture, stop firmware, then release host resources."""
        if not self._running:
            return
        self._armed = False
        self._running = False
        self._stop_event.set()
        if self._button is not None:
            self._button.when_pressed = None
            self._button.close()
            self._button = None
        try:
            self._events.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            # Preserve a complete debug dump and its trailing CLI prompt before
            # issuing sensorStop. Closing early strands firmware mid-transfer.
            self._worker.join(timeout=_GRACEFUL_DUMP_SHUTDOWN_S)
            if self._worker.is_alive():
                logger.warning(
                    "[IWR6843] Active dump did not finish within %.1fs; "
                    "forcing serial close (board reset may be required)",
                    _GRACEFUL_DUMP_SHUTDOWN_S,
                )
                self.radar.close()
                self._worker.join(timeout=2.0)
            else:
                self._stop_sensor_and_close()
            self._worker = None
        else:
            self._stop_sensor_and_close()
        logger.info("[IWR6843] Capture monitor stopped")

    def _stop_sensor_and_close(self) -> None:
        """Best-effort firmware stop that never leaks the serial descriptor."""
        try:
            self.radar.stop_sensor()
            logger.info("[IWR6843] Firmware capture stopped and verified inactive")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "[IWR6843] Firmware did not stop cleanly; board reset may be required",
                exc_info=True,
            )
        finally:
            self.radar.close()


__all__ = [
    "IWR6843Capture",
    "IWR6843CaptureMonitor",
    "tx_order_from_config",
]
