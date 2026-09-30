"""Score an OpenFlight session against a TrackMan / GC reference export.

Usage::

    uv run python scripts/analysis/compare_sessions.py \\
        --openflight session_logs/session_20260506_152406_range.jsonl \\
        --reference ~/Downloads/TrackMan_2026-05-06.csv \\
        [--pair-by order|timestamp] [--json]

Prints, per club and overall, the bias (mean signed error, OpenFlight minus
reference) and MAE for ball speed, club speed, launch angle, spin and carry,
plus how many shots each side had and how many paired up.

Pairing
-------
``order`` (default) reuses ``compare_trackman.pair_shots``: shots are grouped
by club and paired in chronological order within each club, and pairs whose
ball speeds differ by more than ``--ball-speed-tol`` are flagged
``ball_speed_mismatch`` and excluded from the statistics.

``timestamp`` pairs each OpenFlight shot with the reference shot whose
timestamp is nearest (closest pairs claimed first) within
``--time-tolerance-s``. ``--time-offset-s`` absorbs a constant clock skew
between the two systems (OpenFlight minus reference). Clubs are not required
to agree; a pair whose clubs differ is noted.

The loaders (JSONL ``shot_detected`` entries; TrackMan / GC CSV with header
aliases and unit detection) come from ``compare_trackman.py``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_trackman import (  # noqa: E402  pylint: disable=wrong-import-position
    Pair,
    Shot,
    load_openflight,
    load_trackman,
    normalize_club,
    pair_shots,
)

METRICS: List[Tuple[str, str, str]] = [
    ("ball_speed_mph", "ball speed", "mph"),
    ("club_speed_mph", "club speed", "mph"),
    ("launch_angle_vertical", "launch angle", "deg"),
    ("spin_rpm", "spin", "rpm"),
    ("carry_yards", "carry", "yds"),
]

MATCH_QUALITIES = (
    "good",
    "ball_speed_mismatch",
    "unmatched_openflight",
    "unmatched_trackman",
)


# ---------------------------------------------------------------------------
# Timestamp pairing
# ---------------------------------------------------------------------------


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _ball_speed_quality(of: Shot, ref: Shot, ball_speed_tol_mph: float) -> Tuple[str, str]:
    if of.ball_speed_mph is None or ref.ball_speed_mph is None:
        return "good", ""
    delta = abs(of.ball_speed_mph - ref.ball_speed_mph)
    if delta > ball_speed_tol_mph:
        return (
            "ball_speed_mismatch",
            f"ball-speed delta {delta:.1f} mph exceeds tol {ball_speed_tol_mph} mph",
        )
    return "good", ""


def pair_by_timestamp(
    of_shots: List[Shot],
    ref_shots: List[Shot],
    *,
    tolerance_s: float,
    offset_s: float = 0.0,
    ball_speed_tol_mph: float = 5.0,
) -> List[Pair]:
    """Greedy nearest-timestamp pairing across clubs.

    ``offset_s`` is the expected OpenFlight-minus-reference clock difference.
    Shots without a timestamp on either side are reported as unmatched.
    """
    candidates: List[Tuple[float, int, int]] = []
    for i, of in enumerate(of_shots):
        if of.timestamp is None:
            continue
        for j, ref in enumerate(ref_shots):
            if ref.timestamp is None:
                continue
            skew = (_naive(of.timestamp) - _naive(ref.timestamp)).total_seconds() - offset_s
            if abs(skew) <= tolerance_s:
                candidates.append((abs(skew), i, j))
    candidates.sort()

    used_of: set[int] = set()
    used_ref: set[int] = set()
    matched: List[Tuple[int, Pair]] = []
    for skew, i, j in candidates:
        if i in used_of or j in used_ref:
            continue
        used_of.add(i)
        used_ref.add(j)
        of, ref = of_shots[i], ref_shots[j]
        quality, notes = _ball_speed_quality(of, ref, ball_speed_tol_mph)
        detail = f"dt {skew:.1f}s"
        if of.club != ref.club:
            detail += f"; club differs ({of.club or '?'} vs {ref.club or '?'})"
        notes = f"{detail}; {notes}" if notes else detail
        matched.append((i, Pair(of=of, tm=ref, match_quality=quality, notes=notes)))

    pairs = [pair for _, pair in sorted(matched, key=lambda item: item[0])]
    for i, of in enumerate(of_shots):
        if i not in used_of:
            pairs.append(
                Pair(
                    of=of,
                    tm=None,
                    match_quality="unmatched_openflight",
                    notes="no reference shot within the time tolerance",
                )
            )
    for j, ref in enumerate(ref_shots):
        if j not in used_ref:
            pairs.append(
                Pair(
                    of=None,
                    tm=ref,
                    match_quality="unmatched_trackman",
                    notes="no OpenFlight shot within the time tolerance",
                )
            )
    return pairs


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _pair_club(pair: Pair) -> str:
    shot = pair.of or pair.tm
    return (shot.club if shot else "") or "(no club)"


def metric_errors(pairs: List[Pair], field_name: str) -> List[float]:
    """OpenFlight-minus-reference errors over good pairs with both values."""
    errors: List[float] = []
    for pair in pairs:
        if pair.match_quality != "good" or pair.of is None or pair.tm is None:
            continue
        of_value = getattr(pair.of, field_name)
        ref_value = getattr(pair.tm, field_name)
        if of_value is None or ref_value is None:
            continue
        errors.append(of_value - ref_value)
    return errors


def summarize_errors(errors: List[float]) -> Dict[str, Any]:
    if not errors:
        return {"n": 0, "bias": None, "mae": None}
    return {
        "n": len(errors),
        "bias": statistics.fmean(errors),
        "mae": statistics.fmean(abs(e) for e in errors),
    }


def _metric_block(pairs: List[Pair]) -> Dict[str, Dict[str, Any]]:
    return {
        field_name: summarize_errors(metric_errors(pairs, field_name))
        for field_name, _, _ in METRICS
    }


def build_report(
    pairs: List[Pair],
    *,
    openflight_shots: int,
    reference_shots: int,
    pairing: str,
) -> Dict[str, Any]:
    counts = {quality: 0 for quality in MATCH_QUALITIES}
    for pair in pairs:
        counts[pair.match_quality] = counts.get(pair.match_quality, 0) + 1
    counts["pairs"] = len(pairs)

    by_club: Dict[str, List[Pair]] = {}
    for pair in pairs:
        by_club.setdefault(_pair_club(pair), []).append(pair)

    clubs = {}
    for club, club_pairs in sorted(by_club.items()):
        clubs[club] = {
            "pairs": len(club_pairs),
            "good": sum(1 for p in club_pairs if p.match_quality == "good"),
            "metrics": _metric_block(club_pairs),
        }

    return {
        "pairing": pairing,
        "openflight_shots": openflight_shots,
        "reference_shots": reference_shots,
        "counts": counts,
        "clubs": clubs,
        "overall": {
            "pairs": len(pairs),
            "good": counts["good"],
            "metrics": _metric_block(pairs),
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _format_metric_rows(metrics: Dict[str, Dict[str, Any]]) -> List[str]:
    rows = []
    for field_name, label, unit in METRICS:
        stats = metrics[field_name]
        if stats["n"] == 0:
            rows.append(f"    {label:<13} {'n/a':>10}  {'n/a':>10}  (n=0)")
            continue
        rows.append(
            f"    {label:<13} {stats['bias']:>+10.2f}  {stats['mae']:>10.2f}  "
            f"{unit:<4}(n={stats['n']})"
        )
    return rows


def format_report(report: Dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "=" * 72,
        "  SESSION COMPARISON (OpenFlight - reference)",
        "=" * 72,
        f"  Pairing:                {report['pairing']}",
        f"  OpenFlight shots:       {report['openflight_shots']}",
        f"  Reference shots:        {report['reference_shots']}",
        f"  Pairs:                  {counts['pairs']}",
        f"  Good:                   {counts['good']}",
        f"  Ball-speed mismatch:    {counts['ball_speed_mismatch']}",
        f"  Unmatched OpenFlight:   {counts['unmatched_openflight']}",
        f"  Unmatched reference:    {counts['unmatched_trackman']}",
        "",
    ]
    header = f"    {'metric':<13} {'bias':>10}  {'MAE':>10}"
    for club, club_report in report["clubs"].items():
        lines.append(f"  {club} - {club_report['good']} good pair(s) of {club_report['pairs']}")
        lines.append(header)
        lines.extend(_format_metric_rows(club_report["metrics"]))
        lines.append("")
    overall = report["overall"]
    lines.append(f"  ALL CLUBS - {overall['good']} good pair(s) of {overall['pairs']}")
    lines.append(header)
    lines.extend(_format_metric_rows(overall["metrics"]))
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_pairs(
    of_shots: List[Shot],
    ref_shots: List[Shot],
    *,
    pair_by: str,
    ball_speed_tol_mph: float,
    time_tolerance_s: float,
    time_offset_s: float,
    club_filter: Optional[List[str]],
) -> List[Pair]:
    if pair_by == "order":
        return pair_shots(
            of_shots,
            ref_shots,
            ball_speed_tol_mph=ball_speed_tol_mph,
            club_filter=club_filter,
        )
    if club_filter:
        wanted = {normalize_club(c) for c in club_filter}
        of_shots = [s for s in of_shots if s.club in wanted]
        ref_shots = [s for s in ref_shots if s.club in wanted]
    return pair_by_timestamp(
        of_shots,
        ref_shots,
        tolerance_s=time_tolerance_s,
        offset_s=time_offset_s,
        ball_speed_tol_mph=ball_speed_tol_mph,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-club bias and MAE of an OpenFlight session against a "
        "TrackMan / GC reference CSV.",
    )
    parser.add_argument(
        "--openflight", required=True, type=Path, help="OpenFlight session JSONL file"
    )
    parser.add_argument(
        "--reference",
        "--trackman",
        dest="reference",
        required=True,
        type=Path,
        help="TrackMan / GC CSV export",
    )
    parser.add_argument(
        "--pair-by",
        choices=("order", "timestamp"),
        default="order",
        help="Pair shots in per-club order (default) or by nearest timestamp",
    )
    parser.add_argument(
        "--ball-speed-tol",
        type=float,
        default=5.0,
        help="Max ball-speed delta (mph) before a pair is flagged as a mis-pair (default 5.0)",
    )
    parser.add_argument(
        "--time-tolerance-s",
        type=float,
        default=30.0,
        help="Max timestamp gap (s) for --pair-by timestamp (default 30)",
    )
    parser.add_argument(
        "--time-offset-s",
        type=float,
        default=0.0,
        help="Expected OpenFlight-minus-reference clock skew in seconds (default 0)",
    )
    parser.add_argument(
        "--club-filter",
        default=None,
        help="Comma-separated clubs to include (e.g. '7-iron,driver'). Default: all.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the report as JSON instead of text"
    )
    args = parser.parse_args(argv)

    if not args.openflight.exists():
        print(f"OpenFlight log not found: {args.openflight}", file=sys.stderr)
        return 2
    if not args.reference.exists():
        print(f"Reference CSV not found: {args.reference}", file=sys.stderr)
        return 2

    of_shots = load_openflight(args.openflight)
    ref_shots = load_trackman(args.reference)
    if args.pair_by == "timestamp":
        for label, shots in (("OpenFlight", of_shots), ("reference", ref_shots)):
            if shots and all(s.timestamp is None for s in shots):
                print(
                    f"warning: no {label} shot has a timestamp; --pair-by timestamp "
                    "will match nothing (use --pair-by order)",
                    file=sys.stderr,
                )
    club_filter = (
        [c.strip() for c in args.club_filter.split(",") if c.strip()] if args.club_filter else None
    )
    pairs = build_pairs(
        of_shots,
        ref_shots,
        pair_by=args.pair_by,
        ball_speed_tol_mph=args.ball_speed_tol,
        time_tolerance_s=args.time_tolerance_s,
        time_offset_s=args.time_offset_s,
        club_filter=club_filter,
    )
    report = build_report(
        pairs,
        openflight_shots=len(of_shots),
        reference_shots=len(ref_shots),
        pairing=args.pair_by,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
