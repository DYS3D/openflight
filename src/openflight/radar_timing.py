"""Timing knobs for the OPS243 capture cycle (clock sync and re-arm).

Every default reproduces the behaviour the capture path shipped with. The
fast paths (``--fast-clock-sync``, ``--rearm-after-handoff``) are opt-in so
a range session can A/B them and fall back without a code change. A dump
that arrives without its ``]}`` terminator reverts the active timing to the
slow defaults for the rest of the session (see ``ActiveRadarTiming``).
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, fields
from typing import Optional

logger = logging.getLogger("openflight.radar_timing")


@dataclass(frozen=True)
class RadarTimingConfig:
    """Timing values used by ``OPS243Radar`` and ``SoundTrigger``.

    Field defaults are the shipped behaviour: 36 ``C?`` samples per shot,
    a 1.25 s cap on the integer-clock rollover search, and the 0.2 / 0.1 /
    0.1 / 0.15 s re-arm sleeps (drain poll, after ``PA``, after ``S#n``,
    after the second ``PA``).
    """

    clock_sync_samples: int = 36
    max_sync_duration_s: float = 1.25
    rearm_drain_poll_s: float = 0.2
    rearm_after_pa_s: float = 0.1
    rearm_after_split_s: float = 0.1
    rearm_after_activate_s: float = 0.15
    fast_clock_sync: bool = False
    rearm_after_handoff: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RadarTimingConfig":
        """Build the config from a namespace parsed with ``add_radar_timing_args``."""
        return cls(**{f.name: getattr(args, f.name) for f in fields(cls)})


SLOW_DEFAULTS = RadarTimingConfig()


class ActiveRadarTiming:
    """Mutable holder for the timing config in force.

    ``requested`` is what the operator asked for; ``active`` is what the
    capture cycle reads. ``enter_safe_mode`` swaps ``active`` back to
    ``SLOW_DEFAULTS`` (fast flags off) for the rest of the session and logs
    a single warning. When the requested timing already equals the slow
    defaults there is nothing to revert, so nothing is logged.
    """

    def __init__(self, requested: Optional[RadarTimingConfig] = None):
        self.requested = requested if requested is not None else SLOW_DEFAULTS
        self.active = self.requested
        self.safe_mode_reason: Optional[str] = None

    @property
    def safe_mode(self) -> bool:
        return self.safe_mode_reason is not None

    def enter_safe_mode(self, reason: str) -> bool:
        """Revert to the slow defaults. Returns True only on the first switch."""
        if self.safe_mode_reason is not None or self.active == SLOW_DEFAULTS:
            return False
        self.safe_mode_reason = reason
        self.active = SLOW_DEFAULTS
        logger.warning(
            "[TIMING] %s — reverting radar timing to the slow defaults for the "
            "rest of the session (requested: %s)",
            reason,
            self.requested,
        )
        return True


def add_radar_timing_args(parser: argparse.ArgumentParser) -> None:
    """Register the timing flags on ``parser``."""
    group = parser.add_argument_group(
        "OPS243 timing",
        "Clock-sync and re-arm timing. Defaults are the shipped behaviour; the "
        "fast paths are opt-in and revert to these defaults for the rest of the "
        "session if a dump arrives without its ]} terminator.",
    )
    group.add_argument(
        "--clock-sync-samples",
        dest="clock_sync_samples",
        type=int,
        default=SLOW_DEFAULTS.clock_sync_samples,
        help=(
            "C? reads per accepted shot when mapping the radar clock to host time "
            f"(default: {SLOW_DEFAULTS.clock_sync_samples})"
        ),
    )
    group.add_argument(
        "--clock-sync-max-duration",
        dest="max_sync_duration_s",
        type=float,
        default=SLOW_DEFAULTS.max_sync_duration_s,
        help=(
            "Seconds to keep sampling an integer-only radar clock while waiting for "
            f"a one-second rollover (default: {SLOW_DEFAULTS.max_sync_duration_s})"
        ),
    )
    group.add_argument(
        "--rearm-drain-poll",
        dest="rearm_drain_poll_s",
        type=float,
        default=SLOW_DEFAULTS.rearm_drain_poll_s,
        help=(
            "Seconds between serial-drain polls before re-arming "
            f"(default: {SLOW_DEFAULTS.rearm_drain_poll_s})"
        ),
    )
    group.add_argument(
        "--rearm-after-pa",
        dest="rearm_after_pa_s",
        type=float,
        default=SLOW_DEFAULTS.rearm_after_pa_s,
        help=f"Seconds to wait after the first PA on re-arm (default: {SLOW_DEFAULTS.rearm_after_pa_s})",
    )
    group.add_argument(
        "--rearm-after-split",
        dest="rearm_after_split_s",
        type=float,
        default=SLOW_DEFAULTS.rearm_after_split_s,
        help=f"Seconds to wait after S#n on re-arm (default: {SLOW_DEFAULTS.rearm_after_split_s})",
    )
    group.add_argument(
        "--rearm-after-activate",
        dest="rearm_after_activate_s",
        type=float,
        default=SLOW_DEFAULTS.rearm_after_activate_s,
        help=(
            "Seconds to wait after the second PA on re-arm "
            f"(default: {SLOW_DEFAULTS.rearm_after_activate_s})"
        ),
    )
    group.add_argument(
        "--fast-clock-sync",
        dest="fast_clock_sync",
        action="store_true",
        default=SLOW_DEFAULTS.fast_clock_sync,
        help=(
            "Stop the per-shot clock sync early once a fractional-clock reply "
            "arrives with under 3 ms of read latency. Default off: always take "
            "every sample."
        ),
    )
    group.add_argument(
        "--rearm-after-handoff",
        dest="rearm_after_handoff",
        action="store_true",
        default=SLOW_DEFAULTS.rearm_after_handoff,
        help=(
            "Hand an accepted capture to shot processing and the UI before "
            "re-arming the radar. Default off: re-arm first, then process."
        ),
    )
