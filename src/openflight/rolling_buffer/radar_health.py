"""OPS243 noise-floor tracking and interference flagging (--interference-check).

In sound-trigger mode the OPS243 keeps its rolling buffer on-chip and sends
nothing over serial until HOST_INT fires, so the host sees no I/Q while the
radar is armed and idle. Reading the port between shots would collide with
the trigger's blocking read (see the serial-deadlock note in
``trigger.SoundTrigger``), so the noise floor is instead sampled from every
buffer dump the radar already sends: accepted shots and rejected false
triggers alike, rate-limited to one sample per ``min_interval_s``.

Each sample is the 10th-percentile FFT magnitude, in dB, over the speed band
above the DC mask (``RollingBufferProcessor.DC_MASK_BINS``, ~15 mph) in both
Doppler directions across the capture's 128-sample windows that end at least
``CLUB_BRANCH_HISTORY_MS`` before the trigger, so club and ball returns stay
out of the estimate. Captures with samples near the ADC rails are skipped:
clipping spreads energy across every bin of the window it lands in.

An exponential baseline follows the floor. ``interference`` is set once the
floor sits more than ``rise_db`` above the baseline for ``consecutive``
samples and cleared when it is back within ``clear_db``. The baseline only
learns from samples that are not elevated, so it does not chase interference.

The capture thread only hands over a reference to the sample arrays; the FFT
runs on a separate daemon thread. The rolling-buffer monitor hands a capture
over only after the shot callback, so the FFT never competes with the shot.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

from .multitaper import repair_clipped_iq
from .processor import RollingBufferProcessor
from .types import IQCapture

logger = logging.getLogger("openflight.rolling_buffer.radar_health")


def noise_floor_db(processor: RollingBufferProcessor, capture: IQCapture) -> Optional[float]:
    """Pre-club in-band FFT magnitude floor of a capture in dB.

    None when the capture is clipped or has no window clear of the club.
    """
    _, clipped_fraction = repair_clipped_iq(capture.i_samples, capture.q_samples)
    if clipped_fraction > 0.0:
        return None
    window = processor.WINDOW_SIZE
    pre_club_ms = capture.trigger_offset_ms - processor.CLUB_BRANCH_HISTORY_MS
    pre_club_samples = int(pre_club_ms * capture.sample_rate_hz / 1000.0)
    available = min(len(capture.i_samples), len(capture.q_samples), pre_club_samples)
    count = max(available, 0) // window
    if count == 0:
        return None
    i_blocks = np.asarray(capture.i_samples[: count * window], dtype=float).reshape(count, window)
    q_blocks = np.asarray(capture.q_samples[: count * window], dtype=float).reshape(count, window)
    magnitude = processor._window_magnitudes(i_blocks, q_blocks)  # pylint: disable=protected-access
    half = processor.FFT_SIZE // 2
    dc_mask = processor.DC_MASK_BINS
    in_band = np.concatenate(
        (magnitude[:, dc_mask:half], magnitude[:, half + 1 : processor.FFT_SIZE - dc_mask]),
        axis=1,
    )
    floor = float(np.percentile(in_band, 10))
    if floor <= 0.0:
        return None
    return float(20.0 * np.log10(floor))


class RadarHealthMonitor:
    """Track the OPS243 noise floor from captures and flag interference."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        processor: RollingBufferProcessor,
        on_change: Optional[Callable[[dict], None]] = None,
        *,
        rise_db: float = 6.0,
        clear_db: float = 3.0,
        consecutive: int = 3,
        baseline_alpha: float = 0.2,
        min_interval_s: float = 2.0,
    ):
        self._processor = processor
        self._on_change = on_change
        self.rise_db = rise_db
        self.clear_db = clear_db
        self.consecutive = consecutive
        self.baseline_alpha = baseline_alpha
        self.min_interval_s = min_interval_s

        self._lock = threading.Lock()
        self._pending: Optional[IQCapture] = None
        self._pending_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_sample_monotonic: Optional[float] = None

        self.interference = False
        self.noise_floor_db: Optional[float] = None
        self.baseline_db: Optional[float] = None
        self.updated_at: Optional[str] = None
        self._elevated_count = 0

    def start(self) -> None:
        """Start the sampling thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name="radar-health",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the sampling thread."""
        self._stop_event.set()
        self._pending_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def observe(self, capture: IQCapture) -> None:
        """Queue a capture for the worker; O(1) on the caller's thread."""
        now = time.monotonic()
        if (
            self._last_sample_monotonic is not None
            and now - self._last_sample_monotonic < self.min_interval_s
        ):
            return
        self._last_sample_monotonic = now
        with self._lock:
            self._pending = capture
        self._pending_event.set()

    def snapshot(self) -> dict:
        """The ``radar_health`` payload for the UI."""
        with self._lock:
            return {
                "interference": self.interference,
                "noise_floor_db": self.noise_floor_db,
                "baseline_db": self.baseline_db,
                "updated_at": self.updated_at,
            }

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            self._pending_event.wait()
            self._pending_event.clear()
            if self._stop_event.is_set():
                return
            with self._lock:
                capture = self._pending
                self._pending = None
            if capture is None:
                continue
            try:
                self.process(capture)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[RADAR-HEALTH] Noise floor sample failed", exc_info=True)

    def process(self, capture: IQCapture) -> Optional[bool]:
        """Sample one capture synchronously; returns the new interference state."""
        floor = noise_floor_db(self._processor, capture)
        if floor is None:
            return None
        changed = False
        with self._lock:
            self.noise_floor_db = floor
            self.updated_at = datetime.now(timezone.utc).isoformat()
            if self.baseline_db is None:
                self.baseline_db = floor
            rise = floor - self.baseline_db
            if rise > self.rise_db:
                self._elevated_count += 1
            else:
                self._elevated_count = 0
                self.baseline_db += self.baseline_alpha * (floor - self.baseline_db)
            if not self.interference and self._elevated_count >= self.consecutive:
                self.interference = True
                changed = True
            elif self.interference and rise <= self.clear_db:
                self.interference = False
                changed = True
            payload = {
                "interference": self.interference,
                "noise_floor_db": self.noise_floor_db,
                "baseline_db": self.baseline_db,
                "updated_at": self.updated_at,
            }
        if changed:
            if self.interference:
                logger.warning(
                    "[RADAR-HEALTH] Interference: noise floor %.1f dB is %.1f dB above "
                    "the %.1f dB baseline",
                    floor,
                    floor - payload["baseline_db"],
                    payload["baseline_db"],
                )
            else:
                logger.info(
                    "[RADAR-HEALTH] Interference cleared: noise floor %.1f dB (baseline %.1f dB)",
                    floor,
                    payload["baseline_db"],
                )
            if self._on_change is not None:
                try:
                    self._on_change(payload)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning("[RADAR-HEALTH] radar_health callback failed", exc_info=True)
        else:
            logger.debug(
                "[RADAR-HEALTH] Noise floor %.1f dB (baseline %.1f dB)",
                floor,
                payload["baseline_db"],
            )
        return self.interference
