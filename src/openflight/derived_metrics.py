"""Display-only metrics derived from a finalized shot and its simulated flight.

Every value carries a ``source`` label. ``"measured"`` means both inputs are
radar measurements (only smash factor qualifies); everything else is
``"estimated"`` because it goes through a model:

- Smash factor: ball speed / club speed (``Shot.smash_factor``).
- Face angle: TrackMan's rule of thumb is that the ball starts close to where
  the face points, so without club path ``face ~= HLA``. With a club path the
  D-plane start-direction ratio ``k`` from ``spin_axis.py`` gives
  ``face = path + (HLA - path) / k`` (https://www.mdpi.com/2504-3900/2/6/249).
- Face to path: ``face - path``.
- Dynamic loft: ``VLA / launch_ratio`` with the per-club TrackMan launch ratio
  from ``spin_axis.py`` (https://www.mdpi.com/2504-3900/49/1/27).
- Spin loft: ``dynamic_loft - attack_angle``; the measured attack angle when
  the shot has one, else the per-club TrackMan Tour average prior.
- Apex, hang time, landing angle, descent speed and side (+right) come
  straight from the RK4 ``Trajectory`` the server already simulates.
- Curve: lateral displacement beyond the straight-line start direction,
  ``side - carry * tan(HLA)``.
- Roll and total: the ``Trajectory.total_yards`` landing-angle heuristic.
- Shot shape: OpenFlight's own thresholds (no vendor publishes theirs).
  ``|curve| <= 3 yd`` is straight, ``> 3`` fade, ``> 15`` slice, ``< -3``
  draw, ``< -15`` hook; a start direction beyond ``+2 deg`` adds "push",
  below ``-2 deg`` adds "pull". Names are for a right-handed player: the
  shot carries no handedness, so nothing is mirrored.
"""

from __future__ import annotations

import math
from typing import Optional

from .ballistics import Trajectory
from .launch_monitor import Shot
from .spin_axis import dplane_club_parameters

CURVE_STRAIGHT_YARDS = 3.0
CURVE_SEVERE_YARDS = 15.0
START_DIRECTION_STRAIGHT_DEG = 2.0

# Strict mode: with no measured start direction or spin axis these collapse to
# the neutral 0° placeholder; with a club-table vertical launch the lofts are
# just the table echoed back.
HORIZONTAL_KEYS = frozenset(
    {"face_angle_deg", "face_to_path_deg", "curve_yards", "side_yards", "shot_shape"}
)
LOFT_KEYS = frozenset({"dynamic_loft_deg", "spin_loft_deg"})


def _metric(value: float, source: str) -> dict:
    return {"value": float(value), "source": source}


def shot_shape(launch_horizontal_deg: float, curve_yards: float) -> str:
    """Name the shot shape from start direction and curve (see module doc)."""
    if curve_yards > CURVE_SEVERE_YARDS:
        curve_name = "slice"
    elif curve_yards > CURVE_STRAIGHT_YARDS:
        curve_name = "fade"
    elif curve_yards < -CURVE_SEVERE_YARDS:
        curve_name = "hook"
    elif curve_yards < -CURVE_STRAIGHT_YARDS:
        curve_name = "draw"
    else:
        curve_name = ""
    if launch_horizontal_deg > START_DIRECTION_STRAIGHT_DEG:
        start_name = "push"
    elif launch_horizontal_deg < -START_DIRECTION_STRAIGHT_DEG:
        start_name = "pull"
    else:
        start_name = ""
    if start_name and curve_name:
        return f"{start_name}-{curve_name}"
    return start_name or curve_name or "straight"


def _club_delivery(shot: Shot) -> dict:
    derived: dict = {}
    params = dplane_club_parameters(shot.club)
    hla = shot.launch_angle_horizontal
    path = shot.club_path_deg
    if hla is not None:
        face = hla if path is None else path + (hla - path) / params.face_weight
        derived["face_angle_deg"] = _metric(face, "estimated")
        if path is not None:
            derived["face_to_path_deg"] = _metric(face - path, "estimated")
    vla = shot.launch_angle_vertical
    if vla is not None:
        dynamic_loft = vla / params.launch_ratio
        attack_angle = (
            params.attack_angle_deg if shot.club_angle_deg is None else shot.club_angle_deg
        )
        derived["dynamic_loft_deg"] = _metric(dynamic_loft, "estimated")
        derived["spin_loft_deg"] = _metric(dynamic_loft - attack_angle, "estimated")
    return derived


def _flight(shot: Shot, trajectory: Trajectory) -> dict:
    derived = {
        "apex_yards": _metric(trajectory.apex_yards, "estimated"),
        "hang_time_s": _metric(trajectory.flight_time_s, "estimated"),
        "landing_angle_deg": _metric(trajectory.landing_angle_deg, "estimated"),
        "descent_speed_mph": _metric(trajectory.landing_speed_mph, "estimated"),
        "side_yards": _metric(trajectory.lateral_yards, "estimated"),
        "roll_yards": _metric(trajectory.total_yards - trajectory.carry_yards, "estimated"),
        "total_yards": _metric(trajectory.total_yards, "estimated"),
    }
    hla = shot.launch_angle_horizontal
    if hla is not None:
        curve = trajectory.lateral_yards - trajectory.carry_yards * math.tan(math.radians(hla))
        derived["curve_yards"] = _metric(curve, "estimated")
        derived["shot_shape"] = {"value": shot_shape(hla, curve), "source": "estimated"}
    return derived


def _strict_omitted_keys(shot: Shot) -> set[str]:
    omitted: set[str] = set()
    if shot.launch_angle_horizontal_source == "estimated" or shot.spin_axis_deg is None:
        omitted |= HORIZONTAL_KEYS
    if shot.launch_angle_vertical_source == "estimated":
        omitted |= LOFT_KEYS
    return omitted


def derive(shot: Shot, trajectory: Optional[Trajectory] = None, strict: bool = False) -> dict:
    """Derived metrics for a shot; keys whose inputs are missing are omitted.

    ``strict`` also omits keys whose angle inputs are placeholders (see
    ``_strict_omitted_keys``) rather than measurements.
    """
    derived: dict = {}
    if shot.smash_factor is not None:
        source = "estimated" if shot.mode == "mock" else "measured"
        derived["smash_factor"] = _metric(shot.smash_factor, source)
    derived.update(_club_delivery(shot))
    if trajectory is not None:
        derived.update(_flight(shot, trajectory))
    if strict:
        for key in _strict_omitted_keys(shot):
            derived.pop(key, None)
    return derived


def format_for_log(derived: dict) -> str:
    """One-line ``key=value`` summary, e.g. for the per-shot INFO log."""
    parts = []
    for key, entry in derived.items():
        value = entry["value"]
        text = value if isinstance(value, str) else f"{value:.1f}"
        parts.append(f"{key}={text}")
    return " ".join(parts)
