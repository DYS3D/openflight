"""Everything the home page shows, in one response."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from . import stats
from .clubs import club_name, club_sort_key
from .store import Store

YARDS_PER_MILE = 1760
CALENDAR_DAYS = 26 * 7
# A club needs this many shots in both windows before its change is reported.
TREND_MIN_SHOTS = 5
TREND_RECENT_DAYS = 30
TREND_BASELINE_DAYS = 120
# Smallest change worth calling out, per metric.
TREND_THRESHOLDS = {"ball_speed": 1.5, "carry": 4.0, "club_speed": 1.5}
TREND_LABELS = {"ball_speed": "Ball speed", "carry": "Carry", "club_speed": "Club speed"}
TREND_UNITS = {"ball_speed": "mph", "carry": "yd", "club_speed": "mph"}


def _parse(timestamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return None


def side_yards(carry: float | None, launch_h: float | None) -> float | None:
    """Where the ball lands left (-) or right (+) of the target line, from start direction alone."""
    if carry is None or launch_h is None:
        return None
    return carry * math.tan(math.radians(launch_h))


def _rows(store: Store, profile: str | None) -> list:
    clause, params = stats.profile_clause(profile)
    return store.query(
        "SELECT sh.*, s.started_at FROM shots sh JOIN sessions s ON s.id = sh.session_id "
        f"WHERE 1 = 1 {clause} ORDER BY sh.timestamp",
        params,
    )


def _range_view(rows: list, since: datetime | None) -> dict:
    shots = []
    by_club: dict[str, list[float]] = defaultdict(list)
    without_direction = 0
    for row in rows:
        stamp = _parse(row["timestamp"])
        if since is not None and (stamp is None or stamp < since) or row["carry"] is None:
            continue
        by_club[row["club"]].append(row["carry"])
        side = side_yards(row["carry"], row["launch_h"])
        if side is None:
            without_direction += 1
            continue
        shots.append(
            {
                "club": row["club"],
                "carry": round(row["carry"], 1),
                "side": round(side, 1),
                "ball_speed": row["ball_speed"],
                "timestamp": row["timestamp"],
                "session_id": row["session_id"],
            }
        )
    clubs = [
        {
            "club": club,
            "club_name": club_name(club),
            "median": round(statistics.median(carries)),
            "shots": len(carries),
        }
        for club, carries in sorted(by_club.items(), key=lambda item: club_sort_key(item[0]))
    ]
    return {"shots": shots, "clubs": clubs, "without_direction": without_direction}


def _bag(rows: list) -> list[dict]:
    by_club: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["carry"] is not None:
            by_club[row["club"]].append(row["carry"])
    return [
        {
            "club": club,
            "club_name": club_name(club),
            "median": round(statistics.median(carries)),
            "spread": round(statistics.stdev(carries)) if len(carries) > 1 else None,
            "longest": round(max(carries)),
            "shots": len(carries),
        }
        for club, carries in sorted(by_club.items(), key=lambda item: club_sort_key(item[0]))
    ]


def _calendar(rows: list, today: datetime) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        stamp = _parse(row["timestamp"])
        if stamp is not None:
            counts[stamp.date().isoformat()] += 1
    start = today.date() - timedelta(days=CALENDAR_DAYS - 1)
    return [
        {"date": day.isoformat(), "shots": counts.get(day.isoformat(), 0)}
        for day in (start + timedelta(days=offset) for offset in range(CALENDAR_DAYS))
    ]


def _trends(rows: list, today: datetime) -> list[dict]:
    """Biggest changes: the last 30 days against the 90 days before them."""
    recent_start = today - timedelta(days=TREND_RECENT_DAYS)
    baseline_start = today - timedelta(days=TREND_BASELINE_DAYS)
    windows: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        stamp = _parse(row["timestamp"])
        if stamp is None or stamp < baseline_start:
            continue
        window = "recent" if stamp >= recent_start else "baseline"
        for metric in TREND_THRESHOLDS:
            if row[metric] is not None:
                windows[(row["club"], metric, window)].append(row[metric])

    found = []
    for (club, metric, window), recent in windows.items():
        if window != "recent":
            continue
        baseline = windows.get((club, metric, "baseline"), [])
        if len(recent) < TREND_MIN_SHOTS or len(baseline) < TREND_MIN_SHOTS:
            continue
        delta = statistics.median(recent) - statistics.median(baseline)
        if abs(delta) < TREND_THRESHOLDS[metric]:
            continue
        found.append(
            {
                "club": club,
                "club_name": club_name(club),
                "metric": metric,
                "label": TREND_LABELS[metric],
                "unit": TREND_UNITS[metric],
                "recent": round(statistics.median(recent), 1),
                "baseline": round(statistics.median(baseline), 1),
                "delta": round(delta, 1),
                "score": abs(delta) / TREND_THRESHOLDS[metric],
            }
        )
    found.sort(key=lambda trend: trend["score"], reverse=True)
    picked, clubs = [], set()
    for trend in found:
        if trend["club"] in clubs:
            continue
        clubs.add(trend["club"])
        picked.append({key: value for key, value in trend.items() if key != "score"})
    return picked[:3]


def _recent_bests(store: Store, profile: str | None) -> list[dict]:
    bests = []
    for record in stats.records(store, profile):
        for metric, label, unit in (
            ("ball_speed", "Ball speed", "mph"),
            ("club_speed", "Club speed", "mph"),
            ("carry", "Carry", "yd"),
        ):
            best = record[metric]
            if best is not None:
                bests.append(
                    best
                    | {
                        "club_name": record["club_name"],
                        "metric": metric,
                        "label": label,
                        "unit": unit,
                    }
                )
    bests.sort(key=lambda best: best["timestamp"], reverse=True)
    return bests[:4]


def home(store: Store, profile: str | None, range_days: int | None, today: datetime) -> dict:
    rows = _rows(store, profile)
    carries = [row["carry"] for row in rows if row["carry"] is not None]
    days = {row["timestamp"][:10] for row in rows}
    last_session = None
    if rows:
        last_id = max(rows, key=lambda row: row["started_at"])["session_id"]
        last_rows = [row for row in rows if row["session_id"] == last_id]
        last_session = {
            "id": last_id,
            "started_at": last_rows[0]["started_at"],
            "shots": len(last_rows),
            "clubs": [
                club_name(club)
                for club in sorted({row["club"] for row in last_rows}, key=club_sort_key)
            ],
            "top_ball_speed": max(row["ball_speed"] for row in last_rows),
        }
    since = today - timedelta(days=range_days) if range_days else None
    return {
        "lifetime": {
            "shots": len(rows),
            "sessions": len({row["session_id"] for row in rows}),
            "practice_days": len(days),
            "carry_yards": round(sum(carries)),
            "carry_miles": round(sum(carries) / YARDS_PER_MILE, 1),
            "first_shot": rows[0]["timestamp"] if rows else None,
        },
        "last_session": last_session,
        "range": _range_view(rows, since) | {"days": range_days},
        "bag": _bag(rows),
        "trends": _trends(rows, today),
        "recent_bests": _recent_bests(store, profile),
        "calendar": _calendar(rows, today),
    }
