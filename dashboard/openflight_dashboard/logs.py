"""Turn one OpenFlight session log (JSON lines) into a session and its shots."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

# Mock shots are simulated; swing-speed reps have no ball flight.
_SKIPPED_MODES = {"mock", "swing-speed"}
SWING_LENGTHS = ("full", "3/4", "1/2")
_NAME_STAMP = re.compile(r"^session_(\d{8})_(\d{6})")


@dataclass
class Shot:
    timestamp: str
    shot_number: int | None
    club: str
    profile_id: str | None
    profile_name: str | None
    ball_speed: float
    club_speed: float | None
    smash: float | None
    carry: float | None
    launch_v: float | None
    launch_h: float | None
    spin: float | None
    spin_axis: float | None
    # Wedge matrix tag from the kiosk: "full", "3/4" or "1/2".
    swing: str | None = None


@dataclass
class ParsedSession:
    session_id: str
    started_at: str
    location: str | None
    shots: list[Shot] = field(default_factory=list)


def session_id_for(name: str) -> str:
    return name.removesuffix(".jsonl")


def _started_from_name(name: str) -> str | None:
    match = _NAME_STAMP.match(name)
    if not match:
        return None
    # The Pi names files in its local time, like every other timestamp it logs.
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").isoformat()  # noqa: DTZ007


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _shot(entry: dict) -> Shot | None:
    if entry.get("mode") in _SKIPPED_MODES or entry.get("club") == "Swing Speed":
        return None
    ball_speed = _number(entry.get("ball_speed_mph"))
    timestamp = entry.get("timestamp")
    if ball_speed is None or not isinstance(timestamp, str):
        return None
    carry = _number(entry.get("carry_spin_adjusted"))
    if carry is None:
        carry = _number(entry.get("estimated_carry_yards"))
    shot_number = entry.get("shot_number")
    return Shot(
        timestamp=timestamp,
        shot_number=shot_number if isinstance(shot_number, int) else None,
        club=str(entry.get("club") or "unknown"),
        profile_id=entry.get("profile_id") or None,
        profile_name=entry.get("profile_name") or None,
        ball_speed=ball_speed,
        club_speed=_number(entry.get("club_speed_mph")),
        smash=_number(entry.get("smash_factor")),
        carry=carry,
        launch_v=_number(entry.get("launch_angle_vertical")),
        launch_h=_number(entry.get("launch_angle_horizontal")),
        spin=_number(entry.get("spin_rpm")),
        spin_axis=_number(entry.get("spin_axis_deg")),
        swing=entry.get("swing_length") if entry.get("swing_length") in SWING_LENGTHS else None,
    )


def parse_session_log(name: str, text: str) -> ParsedSession:
    """Parse a log, dropping shots the user deleted on the unit.

    The Pi may still be writing the file, so a torn last line is skipped
    rather than treated as an error.
    """
    started_at = None
    location = None
    shots: dict[str, Shot] = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "session_start":
            started_at = entry.get("ts") or started_at
            location = entry.get("location") or location
        elif kind == "shot_detected":
            shot = _shot(entry)
            if shot is not None:
                shots[shot.timestamp] = shot
        elif kind == "shot_deleted":
            shots.pop(str(entry.get("shot_timestamp")), None)
        if started_at is None and isinstance(entry.get("ts"), str):
            started_at = entry["ts"]

    return ParsedSession(
        session_id=session_id_for(name),
        started_at=started_at or _started_from_name(name) or "",
        location=location,
        shots=sorted(shots.values(), key=lambda shot: shot.timestamp),
    )
