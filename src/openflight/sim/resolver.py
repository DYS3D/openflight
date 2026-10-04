"""Translate a Shot into a protocol-neutral ResolvedShot with provenance.

This is the single place the measured-vs-estimated fallback table lives, so
every simulator connector inherits identical fallback behavior. Codecs only
serialize the ResolvedShot into their own wire format.
"""

import math
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

from openflight.clubs import ClubType
from openflight.clubs.personal import fallback_spin_rpm
from openflight.clubs.physics import CLUB_PHYSICS, get_club_physics
from openflight.launch_monitor import MODELLED_SPIN_SOURCES, Shot, spin_is_trusted
from openflight.sim.types import IncompleteShotError, PlayerState, ResolvedShot

# Per-club fallback spin (rpm) when no trusted spin exists. Shared with the
# ballistics engine so the carry the UI shows and the spin a simulator flies
# come from the same table.
SPIN_MODEL_RPM: Mapping[ClubType, float] = MappingProxyType(
    {club: physics.typical_spin_rpm for club, physics in CLUB_PHYSICS.items()}
)

# Sources that mean "modeled, not observed".
_ESTIMATED_ANGLE_SOURCES = frozenset({"estimated", "mock"})


def _resolve_total_spin(shot: Shot) -> Tuple[float, str]:
    """High-confidence spin if present, else the per-club model.

    Kinematically calculated or mock spin is a model output, so it is used
    but tagged "estimated".
    """
    if spin_is_trusted(shot):
        provenance = "estimated" if shot.spin_source in MODELLED_SPIN_SOURCES else "measured"
        return float(shot.spin_rpm), provenance
    return fallback_spin_rpm(shot.club), "estimated"


def _angle_provenance(source: Optional[str]) -> str:
    return "estimated" if source in _ESTIMATED_ANGLE_SOURCES else "measured"


def resolve_shot(shot: Shot, player_state: PlayerState) -> ResolvedShot:
    """Fill every simulator field from a Shot, applying the fallback table.

    Raises IncompleteShotError when ball speed is missing — the one field that
    has no honest model. Allocates exactly one shot number per physical shot
    (so all connectors share a number for the same shot).
    """
    if shot.ball_speed_mph is None or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")

    provenance: Dict[str, str] = {"ball_speed": "measured"}

    if shot.launch_angle_vertical is not None:
        vla = float(shot.launch_angle_vertical)
        provenance["vla"] = _angle_provenance(shot.launch_angle_vertical_source)
    else:
        vla = get_club_physics(shot.club).optimal_launch_deg
        provenance["vla"] = "estimated"

    if shot.launch_angle_horizontal is not None:
        hla = float(shot.launch_angle_horizontal)
        provenance["hla"] = _angle_provenance(shot.launch_angle_horizontal_source)
    else:
        hla = 0.0
        provenance["hla"] = "estimated"

    total_spin, spin_prov = _resolve_total_spin(shot)
    provenance["total_spin"] = spin_prov

    if shot.spin_axis_deg is not None:
        spin_axis = float(shot.spin_axis_deg)
        axis_prov = "estimated" if shot.spin_axis_source == "estimated" else "measured"
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = (
        "measured" if (spin_prov == "measured" and axis_prov == "measured") else "estimated"
    )
    provenance["back_spin"] = derived_prov
    provenance["side_spin"] = derived_prov

    # carry_spin_adjusted holds the server's committed carry (ballistic
    # simulator, or the spin table when the simulator cannot run). The bare
    # launch-angle table is only for shots that never went through finalization.
    if shot.carry_spin_adjusted is not None:
        carry = float(shot.carry_spin_adjusted)
    else:
        carry = float(shot.estimated_carry_yards)
    # Carry is never observed by any sensor; it is always a model output.
    provenance["carry"] = "estimated"

    if shot.club_speed_mph is not None and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        provenance["club_speed"] = "measured"
    else:
        club_speed = None
        provenance["club_speed"] = "estimated"

    if shot.club_path_deg is not None:
        club_path = float(shot.club_path_deg)
        provenance["club_path"] = "measured"
    else:
        club_path = 0.0
        provenance["club_path"] = "estimated"

    return ResolvedShot(
        shot_number=player_state.next_shot_number(),
        ball_speed_mph=float(shot.ball_speed_mph),
        vla=vla,
        hla=hla,
        total_spin_rpm=total_spin,
        spin_axis_deg=spin_axis,
        back_spin_rpm=back_spin,
        side_spin_rpm=side_spin,
        carry_yards=carry,
        club_path_deg=club_path,
        club=shot.club,
        club_speed_mph=club_speed,
        provenance=provenance,
    )
