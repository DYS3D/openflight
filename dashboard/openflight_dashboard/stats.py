"""Read-side summaries for the dashboard pages."""

from __future__ import annotations

import statistics
from collections import defaultdict

from .clubs import club_name, club_sort_key
from .store import Store

# Smash factor above this is physically impossible, so the club speed was a
# bad read; such shots never set a club speed record.
MAX_PLAUSIBLE_SMASH = 1.6

TREND_METRICS = ("carry", "ball_speed", "club_speed", "smash", "launch_v", "spin")


def profile_clause(profile: str | None) -> tuple[str, tuple]:
    return ("AND sh.profile_id = ?", (profile,)) if profile else ("", ())


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def profiles(store: Store) -> list[dict]:
    rows = store.query(
        "SELECT profile_id, MAX(profile_name) AS name, COUNT(*) AS shots FROM shots "
        "WHERE profile_id IS NOT NULL GROUP BY profile_id ORDER BY shots DESC"
    )
    return [{"id": row["profile_id"], "name": row["name"], "shots": row["shots"]} for row in rows]


def summary(store: Store) -> dict:
    counts = store.query(
        "SELECT (SELECT COUNT(DISTINCT session_id) FROM shots) AS sessions, "
        "(SELECT COUNT(*) FROM shots) AS shots"
    )[0]
    return {
        "sessions": counts["sessions"],
        "shots": counts["shots"],
        "last_sync": store.get_state("last_sync"),
        "last_error": store.get_state("last_error"),
    }


def sessions(store: Store, profile: str | None) -> list[dict]:
    clause, params = profile_clause(profile)
    rows = store.query(
        "SELECT s.id, s.started_at, sh.club, sh.ball_speed, sh.carry FROM sessions s "
        f"JOIN shots sh ON sh.session_id = s.id WHERE 1 = 1 {clause}",
        params,
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        session = grouped.setdefault(
            row["id"],
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "clubs": set(),
                "ball": [],
                "carry": [],
            },
        )
        session["clubs"].add(row["club"])
        session["ball"].append(row["ball_speed"])
        if row["carry"] is not None:
            session["carry"].append(row["carry"])
    result = [
        {
            "id": session["id"],
            "started_at": session["started_at"],
            "shots": len(session["ball"]),
            "clubs": [club_name(club) for club in sorted(session["clubs"], key=club_sort_key)],
            "median_ball_speed": _round(_median(session["ball"])),
            "median_carry": _round(_median(session["carry"]), 0),
        }
        for session in grouped.values()
    ]
    return sorted(result, key=lambda session: session["started_at"], reverse=True)


def session_shots(store: Store, session_id: str, profile: str | None) -> dict | None:
    meta = store.query("SELECT id, started_at FROM sessions WHERE id = ?", (session_id,))
    if not meta:
        return None
    clause, params = profile_clause(profile)
    rows = store.query(
        f"SELECT sh.* FROM shots sh WHERE sh.session_id = ? {clause} ORDER BY sh.timestamp",
        (session_id, *params),
    )
    shots = [dict(row) | {"club_name": club_name(row["club"])} for row in rows]
    return {"id": meta[0]["id"], "started_at": meta[0]["started_at"], "shots": shots}


def _shots_by_club(store: Store, profile: str | None, since: str | None = None) -> dict:
    clause, params = profile_clause(profile)
    since_clause = "AND sh.timestamp >= ?" if since else ""
    rows = store.query(
        "SELECT sh.*, s.started_at FROM shots sh JOIN sessions s ON s.id = sh.session_id "
        f"WHERE 1 = 1 {clause} {since_clause} ORDER BY sh.timestamp",
        (*params, *((since,) if since else ())),
    )
    by_club: dict[str, list] = defaultdict(list)
    for row in rows:
        by_club[row["club"]].append(row)
    return dict(sorted(by_club.items(), key=lambda item: club_sort_key(item[0])))


def trends(store: Store, profile: str | None) -> list[dict]:
    """Per club, the median of each metric for every session that club was hit in."""
    result = []
    for club, rows in _shots_by_club(store, profile).items():
        per_session: dict[str, list] = defaultdict(list)
        started: dict[str, str] = {}
        for row in rows:
            per_session[row["session_id"]].append(row)
            started[row["session_id"]] = row["started_at"]
        points = []
        for session_id, session_rows in sorted(per_session.items(), key=lambda i: started[i[0]]):
            point = {
                "session_id": session_id,
                "started_at": started[session_id],
                "shots": len(session_rows),
            }
            for metric in TREND_METRICS:
                values = [row[metric] for row in session_rows if row[metric] is not None]
                point[metric] = _round(_median(values), 2 if metric == "smash" else 1)
            points.append(point)
        result.append({"club": club, "club_name": club_name(club), "points": points})
    return result


def is_mishit(row) -> bool:
    return "mishit" in (row["tags"] or "").split(",")


def gapping(
    store: Store, profile: str | None, since: str | None, skip_mishits: bool = False
) -> list[dict]:
    """Carry spread per club: quartiles, so a few mishits do not move the gaps."""
    result = []
    for club, rows in _shots_by_club(store, profile, since).items():
        if skip_mishits:
            rows = [row for row in rows if not is_mishit(row)]
        carries = sorted(row["carry"] for row in rows if row["carry"] is not None)
        if not carries:
            continue
        if len(carries) >= 2:
            low, median, high = statistics.quantiles(carries, n=4, method="inclusive")
        else:
            low = median = high = carries[0]
        result.append(
            {
                "club": club,
                "club_name": club_name(club),
                "shots": len(carries),
                "p25": _round(low, 0),
                "median": _round(median, 0),
                "p75": _round(high, 0),
                "min": _round(carries[0], 0),
                "max": _round(carries[-1], 0),
                "median_ball_speed": _round(_median([row["ball_speed"] for row in rows])),
            }
        )
    return result


def _best(rows: list, metric: str, digits: int) -> dict | None:
    candidates = [row for row in rows if row[metric] is not None]
    if not candidates:
        return None
    row = max(candidates, key=lambda candidate: candidate[metric])
    return {
        "value": _round(row[metric], digits),
        "timestamp": row["timestamp"],
        "session_id": row["session_id"],
    }


def records(store: Store, profile: str | None) -> list[dict]:
    result = []
    for club, rows in _shots_by_club(store, profile).items():
        plausible = [
            row for row in rows if row["smash"] is None or row["smash"] <= MAX_PLAUSIBLE_SMASH
        ]
        result.append(
            {
                "club": club,
                "club_name": club_name(club),
                "shots": len(rows),
                "ball_speed": _best(rows, "ball_speed", 1),
                "club_speed": _best(plausible, "club_speed", 1),
                "carry": _best(rows, "carry", 0),
            }
        )
    return result


WEDGES = ("pw", "gw", "sw", "lw")


def wedge_matrix(store: Store, profile: str | None) -> list[dict]:
    """Median carry per wedge and swing length, from shots tagged on the kiosk."""
    clause, params = profile_clause(profile)
    rows = store.query(
        f"SELECT sh.club, sh.swing, sh.carry FROM shots sh WHERE sh.swing IS NOT NULL "
        f"AND sh.carry IS NOT NULL {clause}",
        params,
    )
    carries: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        carries[(row["club"], row["swing"])].append(row["carry"])
    if not carries:
        return []
    return [
        {
            "club": club,
            "club_name": club_name(club),
            "cells": {
                swing: {
                    "median": _round(_median(carries[(club, swing)]), 0),
                    "shots": len(carries[(club, swing)]),
                }
                for swing in ("full", "3/4", "1/2")
            },
        }
        for club in WEDGES
    ]
