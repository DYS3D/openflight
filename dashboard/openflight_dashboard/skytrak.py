"""Turn a SkyTrak "Shots History" CSV export into a session log the store ingests.

A SkyTrak export starts with ``PRACTICE: <date time>`` and ``PLAYER:`` lines,
two header rows, then one block per club: a row holding only the club name
followed by shot rows (``#, L/R, ...``) and an ``AVG`` row. Each export becomes
one dashboard session named ``session_<YYYYMMDD_HHMMSS>_skytrak``, so importing
the same export again replaces it rather than duplicating it.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import datetime, timedelta

CLUB_IDS = {
    "DRIVER": "driver",
    "3 WOOD": "3-wood",
    "5 WOOD": "5-wood",
    "7 WOOD": "7-wood",
    "9 WOOD": "9-wood",
    "3 HYBRID": "3-hybrid",
    "4 HYBRID": "4-hybrid",
    "5 HYBRID": "5-hybrid",
    "7 HYBRID": "7-hybrid",
    "9 HYBRID": "9-hybrid",
    "2 IRON": "2-iron",
    "3 IRON": "3-iron",
    "4 IRON": "4-iron",
    "5 IRON": "5-iron",
    "6 IRON": "6-iron",
    "7 IRON": "7-iron",
    "8 IRON": "8-iron",
    "9 IRON": "9-iron",
    "PW": "pw",
    "GW": "gw",
    "SW": "sw",
    "LW": "lw",
}
# Shots in one export are spread this far apart so each has its own timestamp.
SHOT_SPACING_S = 30
# Column positions in a shot row.
BALL_SPEED, LAUNCH, BACK_SPIN, SIDE_SPIN, SIDE_DEG, CARRY, CLUB_SPEED, SMASH = (
    3, 4, 5, 6, 7, 9, 15, 16,
)  # fmt: skip


class SkyTrakFormatError(ValueError):
    """The file is not a SkyTrak shots-history export."""


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def club_id(name: str) -> str | None:
    """Dashboard club id for a SkyTrak club heading ("DRIVER 1" counts as driver)."""
    name = " ".join(name.upper().split())
    if name in CLUB_IDS:
        return CLUB_IDS[name]
    base = name.rsplit(" ", 1)[0]
    return CLUB_IDS.get(base) if name.split()[-1].isdigit() else None


def _started_at(rows: list[list[str]]) -> datetime:
    for row in rows:
        if row and row[0].startswith("PRACTICE:"):
            stamp = row[0].split(":", 1)[1].strip()
            try:
                # SkyTrak writes local time, like the Pi's own logs.
                return datetime.strptime(stamp, "%m/%d/%Y %I:%M %p")  # noqa: DTZ007
            except ValueError as exc:
                raise SkyTrakFormatError(f"unreadable PRACTICE date {stamp!r}") from exc
    raise SkyTrakFormatError("no PRACTICE line; is this a SkyTrak shots-history export?")


def convert(text: str, *, profile_id: str | None, profile_name: str | None) -> tuple[str, str, int]:
    """Return (log name, JSON-lines text, shot count) for one SkyTrak CSV export."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    started = _started_at(rows)
    entries: list[dict] = [
        {"type": "session_start", "ts": started.isoformat(), "location": "SkyTrak"}
    ]
    club = None
    count = 0
    for row in rows:
        if not row or not row[0].strip():
            continue
        if all(not cell.strip() for cell in row[1:]):
            heading = club_id(row[0])
            if heading:
                club = heading
            continue
        if club is None or len(row) <= SMASH or row[1] not in ("R", "L"):
            continue
        ball_speed = _number(row[BALL_SPEED])
        if ball_speed is None:
            continue
        back, side = _number(row[BACK_SPIN]), _number(row[SIDE_SPIN])
        count += 1
        stamp = (started + timedelta(seconds=SHOT_SPACING_S * count)).isoformat()
        entries.append(
            {
                "type": "shot_detected",
                "ts": stamp,
                "timestamp": stamp,
                "shot_number": count,
                "mode": "skytrak",
                "club": club,
                "profile_id": profile_id,
                "profile_name": profile_name,
                "ball_speed_mph": ball_speed,
                "club_speed_mph": _number(row[CLUB_SPEED]),
                "smash_factor": _number(row[SMASH]),
                "estimated_carry_yards": _number(row[CARRY]),
                "launch_angle_vertical": _number(row[LAUNCH]),
                "launch_angle_horizontal": _number(row[SIDE_DEG]),
                "spin_rpm": (
                    round(math.hypot(back, side)) if back is not None and side is not None else None
                ),
                "spin_axis_deg": (
                    round(math.degrees(math.atan2(side, back)), 1)
                    if back is not None and side is not None and (back or side)
                    else None
                ),
            }
        )
    if not count:
        raise SkyTrakFormatError("no shots found in the export")
    name = f"session_{started:%Y%m%d_%H%M%S}_skytrak.jsonl"
    return name, "\n".join(json.dumps(entry) for entry in entries) + "\n", count


def golfer_profile(store, golfer: str) -> tuple[str, str]:
    """(profile id, name) for a golfer: the Pi's id when their shots are already here."""
    golfer = golfer.strip()
    if not golfer:
        raise SkyTrakFormatError("name the golfer these shots belong to")
    known = store.profile_id_for_name(golfer)
    if known:
        return known, golfer
    slug = re.sub(r"[^a-z0-9]+", "-", golfer.lower()).strip("-") or "golfer"
    return f"skytrak-{slug}", golfer


def import_export(store, text: str, golfer: str) -> tuple[str, int]:
    """Convert one export and load it under the golfer; returns (session id, shots)."""
    profile_id, profile_name = golfer_profile(store, golfer)
    name, log, _count = convert(text, profile_id=profile_id, profile_name=profile_name)
    stored = store.ingest(name, log, len(log), time.time())
    return name.removesuffix(".jsonl"), stored
