"""Per-golfer calibration: personal fallback spin and a club-speed scale.

``--spin-profile FILE`` (off by default) replaces the TrackMan tour spin
averages used when measured spin is not trusted. The file is JSON mapping club
ids to spin in rpm, e.g. ``{"8-iron": 4570, "driver": 3140}``; clubs it leaves
out keep the tour average. A golfer's own averages (from a reference monitor's
bag mapping) fit their carry far better than tour numbers.

``--club-speed-scale X`` (default 1.0, off) multiplies every OPS club speed by
X, for a radar that consistently under- or over-reads the club head against a
reference monitor. Smash factor follows because it is derived from club speed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from .physics import get_club_physics
from .types import ClubType

SPIN_PROFILE_RANGE_RPM = (500.0, 15000.0)
CLUB_SPEED_SCALE_RANGE = (0.8, 1.25)

_spin_profile: dict[ClubType, float] = {}


def add_personal_calibration_args(parser: argparse.ArgumentParser) -> None:
    """Server flags for personal calibration (both off by default)."""
    parser.add_argument(
        "--spin-profile",
        metavar="FILE",
        help=(
            "JSON of club id to spin rpm used instead of tour averages when "
            'measured spin is not trusted, e.g. {"8-iron": 4570}. Off by default.'
        ),
    )
    parser.add_argument(
        "--club-speed-scale",
        type=float,
        default=1.0,
        help=(
            "Multiply every radar club speed by this factor (0.8-1.25), "
            "calibrated against a reference monitor. Default 1.0 (off)."
        ),
    )


def load_spin_profile(path: str | Path) -> dict[ClubType, float]:
    """Read and validate a spin profile; raises ValueError on bad content."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read spin profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("spin profile must be a JSON object of club id to rpm")
    profile: dict[ClubType, float] = {}
    low, high = SPIN_PROFILE_RANGE_RPM
    for name, rpm in raw.items():
        try:
            club = ClubType(name)
        except ValueError as exc:
            raise ValueError(f"unknown club id in spin profile: {name!r}") from exc
        if isinstance(rpm, bool) or not isinstance(rpm, (int, float)) or not low <= rpm <= high:
            raise ValueError(f"spin for {name} must be {low:.0f}-{high:.0f} rpm, got {rpm!r}")
        profile[club] = float(rpm)
    return profile


def set_spin_profile(profile: Mapping[ClubType, float]) -> None:
    """Install the personal fallback spins (an empty mapping restores tour averages)."""
    _spin_profile.clear()
    _spin_profile.update(profile)


def fallback_spin_rpm(club: ClubType) -> float:
    """Spin to assume when measured spin is not trusted."""
    personal = _spin_profile.get(club)
    return personal if personal is not None else get_club_physics(club).typical_spin_rpm


def validate_club_speed_scale(scale: float) -> float:
    """Return the scale, or raise ValueError when it is outside the sane range."""
    low, high = CLUB_SPEED_SCALE_RANGE
    if not low <= scale <= high:
        raise ValueError(f"--club-speed-scale must be between {low} and {high}, got {scale}")
    return scale
