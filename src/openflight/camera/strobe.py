"""EXPERIMENTAL: double-exposure IR strobe planning for camera spin.

Two short IR flashes inside one camera exposure put two images of the
ball on a single frame with a relative timing known to a few
microseconds (the technique used by GSA Golf and described in Garmin
US20200398138A1; PiTrac spaces its strobed images by ~3.5 ms). Rotation
between the two images then gives spin without any frame-rate limit.

This module only plans and (optionally) emits the pulses. It cannot
produce usable data on the current build:

* the OV9281 runs at 1 ms exposure with continuous illumination, so a
  150 mph ball smears ~67 mm per exposure and both strobe images would be
  buried in that streak; the exposure must drop to the tens of
  microseconds with the strobe supplying all the light;
* there is no IR strobe: an 850 nm LED array driven at several amps for
  tens of microseconds, a MOSFET/LED-driver stage, a GPIO pulse line from
  the Pi (or a Pico / hardware timer, see :class:`GpioPulseEmitter`) and
  a lens without an IR-cut filter are all required.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol

from ..ballistics import CLUB_TYPICAL_SPIN_RPM
from ..clubs import ClubType

logger = logging.getLogger(__name__)

MPH_TO_MPS = 0.44704

DEFAULT_TARGET_ROTATION_DEG = 30.0
# Below ~20 deg the mark barely moves; above ~40 deg the far edge of the
# mark starts wrapping over the limb. The clamp keeps a 12,000 rpm wedge
# and a 1,500 rpm knock-down inside the usable window.
GAP_MIN_S = 0.5e-3
GAP_MAX_S = 3.5e-3
DEFAULT_PULSE_US = 40.0


def expected_spin_rpm(club: ClubType) -> float:
    """Club-typical spin prior used to pick the strobe gap before the shot."""
    return CLUB_TYPICAL_SPIN_RPM.get(club, CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN])


def strobe_gap_for_spin(
    expected_rpm: float, target_deg: float = DEFAULT_TARGET_ROTATION_DEG
) -> float:
    """Seconds between the two flashes so the ball turns ``target_deg``.

    Clamped to [GAP_MIN_S, GAP_MAX_S]; a non-positive spin prior returns
    the maximum gap.
    """
    if target_deg <= 0:
        raise ValueError("target_deg must be positive")
    if expected_rpm <= 0:
        return GAP_MAX_S
    deg_per_s = expected_rpm * 360.0 / 60.0
    return min(GAP_MAX_S, max(GAP_MIN_S, target_deg / deg_per_s))


def exposure_for_speed(ball_speed_mph: float, max_blur_px: float, px_per_m: float) -> float:
    """Longest flash (seconds) that keeps motion blur under ``max_blur_px``.

    ``px_per_m`` is the image scale at the ball's distance. At 150 mph
    and 1,000 px/m a 1 px blur budget allows ~15 us, which is why the
    flash, not the shutter, has to freeze the ball.
    """
    if ball_speed_mph <= 0 or max_blur_px <= 0 or px_per_m <= 0:
        raise ValueError("ball_speed_mph, max_blur_px and px_per_m must be positive")
    return max_blur_px / (ball_speed_mph * MPH_TO_MPS * px_per_m)


@dataclass(frozen=True)
class StrobePlan:
    """Two flashes inside one exposure, timed from the capture trigger."""

    t0_s: float
    gap_s: float
    pulse_us: float = DEFAULT_PULSE_US
    n_pulses: int = 2

    def __post_init__(self) -> None:
        if self.t0_s < 0:
            raise ValueError("t0_s must be non-negative")
        if not GAP_MIN_S <= self.gap_s <= GAP_MAX_S:
            raise ValueError(f"gap_s must be within [{GAP_MIN_S}, {GAP_MAX_S}]")
        if self.pulse_us <= 0 or self.pulse_us * 1e-6 >= self.gap_s:
            raise ValueError("pulse_us must be positive and shorter than the gap")
        if self.n_pulses < 2:
            raise ValueError("n_pulses must be at least 2")

    @property
    def pulse_times_s(self) -> List[float]:
        """Rising-edge times of every pulse, relative to the trigger."""
        return [self.t0_s + i * self.gap_s for i in range(self.n_pulses)]


def plan_strobe(
    club: ClubType,
    *,
    t0_s: float,
    target_deg: float = DEFAULT_TARGET_ROTATION_DEG,
    pulse_us: float = DEFAULT_PULSE_US,
    expected_rpm: Optional[float] = None,
) -> StrobePlan:
    """Build a plan from the club spin prior (or an explicit expected rpm)."""
    rpm = expected_spin_rpm(club) if expected_rpm is None else expected_rpm
    return StrobePlan(t0_s=t0_s, gap_s=strobe_gap_for_spin(rpm, target_deg), pulse_us=pulse_us)


class PulseEmitter(Protocol):  # pylint: disable=too-few-public-methods
    """Fires the pulses of a :class:`StrobePlan` after a trigger."""

    def fire(self, plan: StrobePlan) -> List[float]:
        """Emit the plan's pulses; returns each rising edge as ``time.perf_counter()``."""


class NoOpPulseEmitter:  # pylint: disable=too-few-public-methods
    """Records plans without touching hardware (default and test backend)."""

    def __init__(self) -> None:
        self.fired: List[StrobePlan] = []

    def fire(self, plan: StrobePlan) -> List[float]:
        """Record the plan and return the pulse edges it would have produced."""
        self.fired.append(plan)
        now = time.perf_counter()
        return [now + t for t in plan.pulse_times_s]


def _busy_wait_until(deadline: float) -> None:
    while time.perf_counter() < deadline:
        pass


def _try_raise_priority() -> None:
    """Best effort SCHED_FIFO for the pulse thread; silently keeps default scheduling."""
    setter = getattr(os, "sched_setscheduler", None)
    param_cls = getattr(os, "sched_param", None)
    fifo = getattr(os, "SCHED_FIFO", None)
    if setter is None or param_cls is None or fifo is None:
        return
    try:
        setter(0, fifo, param_cls(50))
    except (OSError, PermissionError):
        pass


class GpioPulseEmitter:  # pylint: disable=too-few-public-methods
    """Drive the strobe line from a Pi GPIO with a busy-wait timed loop.

    Timing is done in a dedicated thread that asks for SCHED_FIFO and
    spins on ``time.perf_counter()``. On a Pi 5 running the full server
    expect tens of microseconds of jitter on each edge and occasional
    multi-hundred-microsecond stalls under load: the pulse spacing (the
    quantity spin depends on) is measured after the fact from the
    returned edge times, but the flash length itself is not guaranteed. A
    Pico or another hardware timer clocking the LED driver, armed by one
    GPIO edge from the Pi, is the better long-term path.

    ``output_factory`` defaults to gpiozero's ``OutputDevice`` on the
    project's lgpio pin factory (see :mod:`openflight.gpio_factory`).
    """

    def __init__(
        self,
        pin_bcm: int,
        *,
        output_factory: Optional[Callable[[int], object]] = None,
    ) -> None:
        self.pin_bcm = pin_bcm
        self._output = (output_factory or self._default_output)(pin_bcm)
        self._lock = threading.Lock()

    @staticmethod
    def _default_output(pin_bcm: int):
        # pylint: disable=import-outside-toplevel
        from gpiozero import OutputDevice  # noqa: PLC0415

        from ..gpio_factory import ensure_lgpio_pin_factory  # noqa: PLC0415

        ensure_lgpio_pin_factory()

        return OutputDevice(pin_bcm, active_high=True, initial_value=False)

    def _emit(self, plan: StrobePlan, start: float, edges: List[float]) -> None:
        _try_raise_priority()
        pulse_s = plan.pulse_us * 1e-6
        for offset in plan.pulse_times_s:
            _busy_wait_until(start + offset)
            self._output.on()
            edges.append(time.perf_counter())
            _busy_wait_until(start + offset + pulse_s)
            self._output.off()

    def fire(self, plan: StrobePlan) -> List[float]:
        """Emit the plan on a timing thread; returns the rising edges observed."""
        edges: List[float] = []
        with self._lock:
            start = time.perf_counter()
            worker = threading.Thread(
                target=self._emit, args=(plan, start, edges), name="strobe-pulse", daemon=True
            )
            worker.start()
            worker.join(timeout=plan.pulse_times_s[-1] + plan.pulse_us * 1e-6 + 0.1)
        if len(edges) == plan.n_pulses:
            spacing_us = (edges[1] - edges[0]) * 1e6
            logger.info(
                "[STROBE] %d pulses on GPIO%d, first-second spacing %.1f us (planned %.1f us)",
                len(edges),
                self.pin_bcm,
                spacing_us,
                plan.gap_s * 1e6,
            )
        else:
            logger.warning("[STROBE] emitted %d of %d pulses", len(edges), plan.n_pulses)
        return edges


def measured_gap_s(edges: List[float]) -> Optional[float]:
    """Actual first-to-second pulse spacing, or None when a pulse was missed."""
    if len(edges) < 2:
        return None
    return edges[1] - edges[0]
