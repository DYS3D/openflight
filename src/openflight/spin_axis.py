"""D-plane spin-axis estimate from start direction, club path and launch angle.

Start direction minus path is not the spin-axis tilt; it understates it
several-fold. The D-plane model recovers the tilt in three steps:

1. Face to path. The ball starts about ``k`` of the way from the path toward
   the face (0.61-0.83; ~0.76 driver, ~0.69 7-iron), so
   ``face_to_path = (HLA - path) / k``.
   Source: https://www.mdpi.com/2504-3900/2/6/249
2. Spin loft. Vertical launch is roughly ``ratio * dynamic_loft`` (TrackMan
   reference ratios ~0.87 driver, ~0.75 7-iron, ~0.70 wedges), and
   ``spin_loft = dynamic_loft - attack_angle``.
   Source: https://www.mdpi.com/2504-3900/49/1/27
3. Tilt. ``tan(tilt) = tan(face_to_path) / tan(spin_loft)``; e.g. 23 deg spin
   loft and 7 deg face to path give a 16.1 deg tilt.
   Sources: https://www.tutelman.com/golf/ballflight/3dlaunch.php and
   https://www.trackman.com/blog/golf/spin-axis

Default attack angles are TrackMan PGA Tour averages (driver -1.3, fairway
wood/hybrid -3.3, 7-iron -4.3, PW -5.0 deg). Per-club values are
interpolated linearly in nominal loft between the anchors below and held at
the end anchors outside them.

Sign convention matches ``Shot.spin_axis_deg``: positive tilts right (fade),
negative left (draw).
"""

from __future__ import annotations

import math
from typing import NamedTuple

from .clubs import ClubType
from .clubs.physics import get_club_physics

SPIN_AXIS_MODELS = ("legacy", "dplane")

SPIN_LOFT_RANGE_DEG = (5.0, 60.0)
MAX_SPIN_AXIS_DEG = 45.0


class DPlaneClubParameters(NamedTuple):
    """Per-club D-plane priors at one nominal loft."""

    loft_deg: float
    face_weight: float
    launch_ratio: float
    attack_angle_deg: float


DPLANE_ANCHORS: tuple[DPlaneClubParameters, ...] = (
    DPlaneClubParameters(10.5, 0.76, 0.87, -1.3),
    DPlaneClubParameters(18.0, 0.73, 0.83, -3.3),
    DPlaneClubParameters(34.0, 0.69, 0.75, -4.3),
    DPlaneClubParameters(46.0, 0.66, 0.70, -5.0),
)


def dplane_club_parameters(club: ClubType) -> DPlaneClubParameters:
    """Interpolate the D-plane priors at the club's nominal loft."""
    loft = get_club_physics(club).nominal_loft_deg
    if loft <= DPLANE_ANCHORS[0].loft_deg:
        return DPLANE_ANCHORS[0]._replace(loft_deg=loft)
    for low, high in zip(DPLANE_ANCHORS, DPLANE_ANCHORS[1:]):
        if loft <= high.loft_deg:
            weight = (loft - low.loft_deg) / (high.loft_deg - low.loft_deg)
            return DPlaneClubParameters(
                *(a + weight * (b - a) for a, b in zip(low, high)),
            )
    return DPLANE_ANCHORS[-1]._replace(loft_deg=loft)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def spin_axis_tilt_deg(face_to_path_deg: float, spin_loft_deg: float) -> float:
    """Spin-axis tilt from face to path and spin loft, clamped to +/-45 deg."""
    face_to_path = math.radians(face_to_path_deg)
    spin_loft = math.radians(_clamp(spin_loft_deg, *SPIN_LOFT_RANGE_DEG))
    # atan2 form of tan(tilt) = tan(ftp) / tan(spin_loft) that keeps the sign past 90 deg.
    tilt = math.degrees(
        math.atan2(
            math.sin(face_to_path) * math.cos(spin_loft),
            math.cos(face_to_path) * math.sin(spin_loft),
        )
    )
    return _clamp(tilt, -MAX_SPIN_AXIS_DEG, MAX_SPIN_AXIS_DEG)


class DPlaneEstimate(NamedTuple):
    """D-plane spin axis and the intermediate quantities used to derive it."""

    spin_axis_deg: float
    face_to_path_deg: float
    dynamic_loft_deg: float
    attack_angle_deg: float
    attack_angle_source: str
    spin_loft_deg: float


def dplane_spin_axis(
    *,
    launch_horizontal_deg: float,
    club_path_deg: float,
    launch_vertical_deg: float,
    club: ClubType,
    attack_angle_deg: float | None = None,
) -> DPlaneEstimate:
    """Estimate spin-axis tilt with the D-plane model.

    ``attack_angle_deg`` should be a measured angle of attack when one is
    trusted; otherwise the club's default attack angle is used.
    """
    params = dplane_club_parameters(club)
    face_to_path = (launch_horizontal_deg - club_path_deg) / params.face_weight
    dynamic_loft = launch_vertical_deg / params.launch_ratio
    if attack_angle_deg is None:
        attack_angle = params.attack_angle_deg
        attack_source = "club_default"
    else:
        attack_angle = attack_angle_deg
        attack_source = "measured"
    spin_loft = _clamp(dynamic_loft - attack_angle, *SPIN_LOFT_RANGE_DEG)
    return DPlaneEstimate(
        spin_axis_deg=spin_axis_tilt_deg(face_to_path, spin_loft),
        face_to_path_deg=face_to_path,
        dynamic_loft_deg=dynamic_loft,
        attack_angle_deg=attack_angle,
        attack_angle_source=attack_source,
        spin_loft_deg=spin_loft,
    )
