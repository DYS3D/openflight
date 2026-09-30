"""Ball-speed cosine correction.

The OPS243 measures RADIAL speed — the component of the ball's velocity
along the radar's line of sight. The ball departs upward at the launch
angle while the radar sits low behind the tee, so the radial reading is
compressed by cos(angle between velocity and LOS). This is the dominant
cause of the long-observed ~2-2.5 mph OPS-below-TrackMan ball speed gap.

Model (zero free parameters): the OPS mode-based extraction reads near
the MAXIMUM of the radial-speed profile — radial speed rises early in
flight as the LOS aligns with the velocity vector, then falls with drag,
so the profile has a peak. The correction divides the measured speed by
that predicted peak fraction.

Validated offline against TrackMan ball speeds (2026-06):
- 2026-06-08 bay, 128 shots: bias -2.15 -> +0.33 mph, |err| 2.83 -> 1.48
- 2026-05-30 holdout, 26 shots: bias -2.26 -> +0.30, |err| 2.35 -> 1.31
- Coleman cross-rig outdoor, 62 shots: bias -2.09 -> +0.65, median 0.58
- Production configuration (OUR launch angles — 100 two-ray + 28
  club-fallback — instead of TrackMan's): bias +0.32, |err| 1.52,
  median 0.67. The LA dependence couples speed accuracy to launch-angle
  accuracy, but LA errors are zero-mean post-calibration, so the
  coupling adds ~0.1 mph of scatter and no bias.

Known caveat: high-launch wedges on one outdoor rig overcorrected (~+3);
under observation. Club speed does NOT need this correction — the club
head's delivery is nearly parallel to the LOS (error ~0.1-0.2 mph).
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

MPH_TO_FTS = 1.4666667
DRAG_MPH_PER_MS = 0.027  # iron-speed drag deceleration of the ball


@dataclass(frozen=True)
class SpeedCorrectionConfig:
    """When the cosine correction runs.

    The correction rides on whichever angle radar supplies launch angle.
    ``apply_without_angle_radar`` also applies it on OPS-only builds using the
    table-estimated launch angle; default off leaves those builds uncorrected.
    """

    apply_without_angle_radar: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "SpeedCorrectionConfig":
        return cls(apply_without_angle_radar=bool(args.speed_correction_without_angle_radar))


def add_speed_correction_args(parser: argparse.ArgumentParser) -> None:
    """Register the speed-correction flag on ``parser``."""
    group = parser.add_argument_group("Ball-speed cosine correction")
    group.add_argument(
        "--speed-correction-without-angle-radar",
        dest="speed_correction_without_angle_radar",
        action="store_true",
        default=False,
        help=(
            "Apply the ball-speed cosine correction on OPS-only builds using the "
            "table-estimated launch angle. Default off: the correction only runs "
            "with --kld7 or --iwr6843."
        ),
    )


def radial_speed_factor(
    launch_angle_deg: float,
    ball_speed_mph: float,
    ball_distance_ft: float,
    ball_above_radar_ft: float,
    window_ms: float = 70.0,
) -> float:
    """Predicted (OPS radial reading) / (true ball speed), in (0, 1].

    Maximum of the radial-speed profile over the capture window:
    radial(t) = v(t) * cos(launch - elevation_of_ball_from_radar(t)).
    """
    if ball_speed_mph <= 0:
        return 1.0
    la = math.radians(launch_angle_deg)
    v_fts = ball_speed_mph * MPH_TO_FTS
    best = 0.0
    t_ms = 0.0
    while t_ms <= window_ms:
        v_frac = max(1.0 - DRAG_MPH_PER_MS * t_ms / ball_speed_mph, 0.0)
        t = t_ms / 1000.0
        x = ball_distance_ft + v_fts * math.cos(la) * t
        y = ball_above_radar_ft + v_fts * math.sin(la) * t
        best = max(best, v_frac * math.cos(la - math.atan2(y, x)))
        t_ms += 2.0
    return min(max(best, 0.5), 1.0)


def correct_ball_speed(
    measured_mph: float,
    launch_angle_deg: float,
    ball_distance_ft: float,
    ball_above_radar_ft: float,
) -> float:
    """True ball speed from the OPS radial measurement."""
    factor = radial_speed_factor(
        launch_angle_deg, measured_mph, ball_distance_ft, ball_above_radar_ft
    )
    return measured_mph / factor
