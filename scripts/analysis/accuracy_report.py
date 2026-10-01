"""Accuracy regression report: OpenFlight vs a reference launch monitor.

Produces the standard agreement statistics used in launch-monitor
validation studies so that model or sensor changes are always judged on
the same footing:

* per metric and per club: n, bias (mean of ours - reference), SD of the
  differences, 95 % Bland-Altman limits of agreement (bias +/- 1.96 SD),
  t-based 95 % confidence intervals on the bias and on each limit, MAE,
  RMSE, ICC(2,1) (two-way random, absolute agreement, single measure)
  and Pearson r;
* PASS / WARN against target bars taken from published launch-monitor
  comparisons, or INCONCLUSIVE when fewer than ``MIN_TARGET_N`` pairs
  back the statistic (not counted in the pass percentage);
* a "tour envelope" sanity check that feeds PGA Tour average launch
  conditions through the ballistic model and reports the % error of
  carry, apex and landing angle against TrackMan's published averages.

Two input sources are supported and can be combined:

1. ``--trackman`` / ``--comparison``: the repo's paired TrackMan capture
   (``session_logs/OpenFlight-Test.Normalized.csv`` and
   ``session_logs/comparison_20260506.csv``), loaded with the same
   loaders as ``validate_ballistics.py`` and
   ``tests/test_ballistics_trackman_regression.py`` so all three agree.
   The TrackMan file alone yields the model-only metrics
   ``carry_model`` and ``apex_model`` (model fed TrackMan's launch
   conditions vs TrackMan's measured carry / apex). The comparison file
   yields the sensor metrics ``ball_speed``, ``club_speed``,
   ``launch_angle``, ``spin`` and ``carry`` (OpenFlight vs TrackMan).
   ``--comparison`` also works on its own, so a new paired session from
   ``compare_trackman.py`` can be scored directly; ``--since`` keeps only
   shots at or after a date/time.
2. ``--csv``: a generic file with columns ``metric, ours, reference[, club]``
   so a comparison against any launch monitor can be pasted in.

Target bars (see ``TARGETS``) come from the Mevo+ vs TrackMan 4 agreement
study (https://www.sciencedirect.com/science/article/pii/S2772696725000420)
and Carl's Place launch-monitor tests. Tour averages come from
https://support.trackmangolf.com/hc/en-us/articles/5089752464667 and
https://teeituprva.com/wp-content/uploads/2019/03/PGA-AVERAGES-INTERACTIVE.pdf.

Usage::

    uv run python scripts/analysis/accuracy_report.py \\
        --trackman session_logs/OpenFlight-Test.Normalized.csv \\
        --comparison session_logs/comparison_20260506.csv

    uv run python scripts/analysis/accuracy_report.py \\
        --comparison ~/openflight_sessions/comparison_20261001.csv --since 2026-10-01

    uv run python scripts/analysis/accuracy_report.py --csv my_pairs.csv \\
        --json --fail-under 80
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
from scipy import stats as scipy_stats

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
_ANALYSIS = Path(__file__).resolve().parent
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

from validate_ballistics import (  # noqa: E402
    _normalize_club,
    _to_float,
    load_comparison,
    load_trackman,
    validate_tm_inputs,
)

from openflight.ballistics import (  # noqa: E402
    AIR_DENSITY_STD,
    LaunchConditions,
    simulate,
)

GENERIC_COLUMNS = ("metric", "ours", "reference")
MIN_TARGET_N = 20


@dataclass
class PairedSample:
    """One (OpenFlight value, reference value) pair for a metric."""

    metric: str
    ours: float
    reference: float
    club: str = ""


@dataclass
class AgreementStats:
    """Agreement statistics for one metric (and optionally one club)."""

    metric: str
    club: str
    n: int
    bias: float
    sd_diff: float
    loa_low: float
    loa_high: float
    mae: float
    rmse: float
    icc: float
    pearson_r: float
    bias_ci_low: float = float("nan")
    bias_ci_high: float = float("nan")
    loa_low_ci_low: float = float("nan")
    loa_low_ci_high: float = float("nan")
    loa_high_ci_low: float = float("nan")
    loa_high_ci_high: float = float("nan")


@dataclass
class TargetCheck:
    metric: str
    club: str
    stat: str
    value: float
    threshold: float
    comparison: str  # "<=" or ">="
    passed: bool
    source: str
    n: int
    status: str  # "PASS", "WARN" or "INCONCLUSIVE" (n < MIN_TARGET_N)


@dataclass
class TourEnvelopeRow:
    club: str
    ball_speed_mph: float
    launch_deg: float
    spin_rpm: float
    carry_ref_yd: float
    carry_model_yd: float
    carry_error_pct: float
    apex_ref_yd: float
    apex_model_yd: float
    apex_error_pct: float
    landing_ref_deg: float
    landing_model_deg: float
    landing_error_pct: float


@dataclass
class Target:
    """A published accuracy bar. ``clubs`` limits the bar to those clubs."""

    metric: str
    stat: str
    comparison: str
    threshold: float
    source: str
    clubs: Optional[tuple] = None


MEVO_STUDY = "Mevo+ vs TrackMan 4 study (S2772696725000420)"
CARLS_PLACE = "Carl's Place launch monitor tests"

TARGETS: List[Target] = [
    Target("ball_speed", "abs_bias", "<=", 1.5, MEVO_STUDY),
    Target("ball_speed", "icc", ">=", 0.99, MEVO_STUDY),
    Target("launch_angle", "abs_bias", "<=", 1.0, MEVO_STUDY),
    Target("launch_angle", "icc", ">=", 0.95, MEVO_STUDY),
    Target("spin", "sd_diff", "<=", 500.0, CARLS_PLACE, clubs=("driver",)),
    Target("spin", "icc", ">=", 0.8, MEVO_STUDY),
    Target("carry", "abs_bias", "<=", 3.0, CARLS_PLACE),
    Target("carry", "sd_diff", "<=", 7.0, CARLS_PLACE),
    Target("club_speed", "abs_bias", "<=", 2.0, MEVO_STUDY),
]

TOUR_AVERAGES: Dict[str, tuple] = {
    "driver": (167.0, 10.9, 2686.0, 275.0, 32.0, 38.0),
    "7-iron": (120.0, 16.3, 7097.0, 172.0, 32.0, 50.0),
    "pw": (102.0, 24.2, 9304.0, 136.0, 29.0, 52.0),
}


def icc_2_1(ratings: Sequence[Sequence[float]]) -> float:
    """ICC(2,1): two-way random effects, absolute agreement, single measure.

    ``ratings`` is n subjects x k raters. Uses the Shrout & Fleiss (1979)
    / McGraw & Wong (1996) mean-square form::

        ICC = (MSR - MSE) / (MSR + (k - 1) MSE + k (MSC - MSE) / n)

    Returns NaN when fewer than two subjects or the denominator is zero.
    """
    data = np.asarray(ratings, dtype=float)
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] < 2:
        return float("nan")
    n, k = data.shape
    grand = data.mean()
    row_means = data.mean(axis=1)
    col_means = data.mean(axis=0)
    ss_rows = k * float(np.sum((row_means - grand) ** 2))
    ss_cols = n * float(np.sum((col_means - grand) ** 2))
    ss_total = float(np.sum((data - grand) ** 2))
    ss_error = ss_total - ss_rows - ss_cols
    ms_rows = ss_rows / (n - 1)
    ms_cols = ss_cols / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))
    denominator = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
    if abs(denominator) < 1e-12:
        return float("nan")
    return (ms_rows - ms_error) / denominator


def agreement_stats(
    ours: Sequence[float], reference: Sequence[float], metric: str = "", club: str = ""
) -> AgreementStats:
    """Bland-Altman, error and reliability statistics for paired values."""
    a = np.asarray(ours, dtype=float)
    b = np.asarray(reference, dtype=float)
    if a.shape != b.shape or a.ndim != 1:
        raise ValueError("ours and reference must be 1-D sequences of equal length")
    n = int(a.size)
    nan = float("nan")
    if n == 0:
        return AgreementStats(metric, club, 0, nan, nan, nan, nan, nan, nan, nan, nan)
    diff = a - b
    bias = float(diff.mean())
    sd_diff = float(diff.std(ddof=1)) if n > 1 else nan
    loa = 1.96 * sd_diff if n > 1 else nan
    mae = float(np.abs(diff).mean())
    rmse = float(math.sqrt(float(np.mean(diff**2))))
    if n > 1 and a.std() > 0 and b.std() > 0:
        pearson = float(np.corrcoef(a, b)[0, 1])
    else:
        pearson = nan
    icc = icc_2_1(np.column_stack([a, b])) if n > 1 else nan
    if n > 1:
        t_crit = float(scipy_stats.t.ppf(0.975, n - 1))
        bias_half = t_crit * sd_diff / math.sqrt(n)
        # Bland & Altman (1999): SE of each limit ~ SD * sqrt(1/n + 1.96^2 / (2 (n - 1))).
        loa_half = t_crit * sd_diff * math.sqrt(1.0 / n + 1.96**2 / (2.0 * (n - 1)))
    else:
        bias_half = loa_half = nan
    return AgreementStats(
        metric=metric,
        club=club,
        n=n,
        bias=bias,
        sd_diff=sd_diff,
        loa_low=bias - loa,
        loa_high=bias + loa,
        mae=mae,
        rmse=rmse,
        icc=icc,
        pearson_r=pearson,
        bias_ci_low=bias - bias_half,
        bias_ci_high=bias + bias_half,
        loa_low_ci_low=bias - loa - loa_half,
        loa_low_ci_high=bias - loa + loa_half,
        loa_high_ci_low=bias + loa - loa_half,
        loa_high_ci_high=bias + loa + loa_half,
    )


def compute_stats(samples: Iterable[PairedSample]) -> List[AgreementStats]:
    """Overall (club == "all") and per-club statistics for every metric."""
    grouped: Dict[str, List[PairedSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.metric, []).append(sample)
    out: List[AgreementStats] = []
    for metric in sorted(grouped):
        rows = grouped[metric]
        out.append(
            agreement_stats(
                [r.ours for r in rows], [r.reference for r in rows], metric=metric, club="all"
            )
        )
        clubs = sorted({r.club for r in rows if r.club})
        for club in clubs:
            club_rows = [r for r in rows if r.club == club]
            out.append(
                agreement_stats(
                    [r.ours for r in club_rows],
                    [r.reference for r in club_rows],
                    metric=metric,
                    club=club,
                )
            )
    return out


def _stat_value(stats: AgreementStats, name: str) -> float:
    if name == "abs_bias":
        return abs(stats.bias)
    return float(getattr(stats, name))


def check_targets(
    stats: Iterable[AgreementStats], targets: Sequence[Target] = tuple(TARGETS)
) -> List[TargetCheck]:
    """Evaluate every target bar that applies to the computed statistics."""
    checks: List[TargetCheck] = []
    for row in stats:
        if row.n < 2:
            continue
        for target in targets:
            if target.metric != row.metric:
                continue
            if target.clubs is not None:
                if row.club not in target.clubs:
                    continue
            elif row.club != "all":
                continue
            value = _stat_value(row, target.stat)
            if math.isnan(value):
                passed = False
            elif target.comparison == "<=":
                passed = value <= target.threshold
            else:
                passed = value >= target.threshold
            if row.n < MIN_TARGET_N:
                status = "INCONCLUSIVE"
            else:
                status = "PASS" if passed else "WARN"
            checks.append(
                TargetCheck(
                    metric=row.metric,
                    club=row.club,
                    stat=target.stat,
                    value=value,
                    threshold=target.threshold,
                    comparison=target.comparison,
                    passed=passed,
                    source=target.source,
                    n=row.n,
                    status=status,
                )
            )
    return checks


def tour_envelope(air_density: float = AIR_DENSITY_STD) -> List[TourEnvelopeRow]:
    """Model carry / apex / landing angle for PGA Tour average launch conditions.

    ``TOUR_AVERAGES`` rows are (ball speed mph, launch deg, spin rpm, carry yd,
    apex yd, landing angle deg).
    """
    rows: List[TourEnvelopeRow] = []
    for club, (speed, launch, spin, carry, apex, landing) in TOUR_AVERAGES.items():
        traj = simulate(
            LaunchConditions(
                ball_speed_mph=speed,
                launch_angle_v=launch,
                launch_angle_h=0.0,
                spin_rpm=spin,
                spin_axis_deg=0.0,
                spin_source="measured",
            ),
            air_density=air_density,
        )
        rows.append(
            TourEnvelopeRow(
                club=club,
                ball_speed_mph=speed,
                launch_deg=launch,
                spin_rpm=spin,
                carry_ref_yd=carry,
                carry_model_yd=traj.carry_yards,
                carry_error_pct=100.0 * (traj.carry_yards - carry) / carry,
                apex_ref_yd=apex,
                apex_model_yd=traj.apex_yards,
                apex_error_pct=100.0 * (traj.apex_yards - apex) / apex,
                landing_ref_deg=landing,
                landing_model_deg=traj.landing_angle_deg,
                landing_error_pct=100.0 * (traj.landing_angle_deg - landing) / landing,
            )
        )
    return rows


def load_generic_csv(path: Path) -> List[PairedSample]:
    """Read ``metric, ours, reference[, club]`` rows; blank values are skipped."""
    samples: List[PairedSample] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = [f.strip().lower() for f in (reader.fieldnames or [])]
        missing = [c for c in GENERIC_COLUMNS if c not in fields]
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(missing)}")
        for raw in reader:
            row = {(k or "").strip().lower(): v for k, v in raw.items()}
            try:
                ours = float(row["ours"])
                reference = float(row["reference"])
            except (TypeError, ValueError):
                continue
            samples.append(
                PairedSample(
                    metric=(row["metric"] or "").strip().lower(),
                    ours=ours,
                    reference=reference,
                    club=_normalize_club(row.get("club")),
                )
            )
    return samples


def load_trackman_pairs(
    trackman_csv: Path, comparison_csv: Optional[Path] = None
) -> List[PairedSample]:
    """Build paired samples from the repo's TrackMan capture files.

    ``carry_model`` / ``apex_model`` compare the ballistic model (fed
    TrackMan's launch conditions) to TrackMan's measured carry (yd) and
    apex (ft), exactly as ``validate_tm_inputs`` reports them. The
    comparison CSV adds OpenFlight-vs-TrackMan sensor metrics for rows
    whose ``match_quality`` is ``good``.
    """
    samples: List[PairedSample] = []
    for row in validate_tm_inputs(load_trackman(trackman_csv)):
        samples.append(
            PairedSample("carry_model", row.model_carry_yards, row.measured_carry_yards, row.club)
        )
        if row.model_apex_feet is not None and row.measured_apex_feet is not None:
            samples.append(
                PairedSample("apex_model", row.model_apex_feet, row.measured_apex_feet, row.club)
            )
    if comparison_csv is not None:
        samples.extend(load_comparison_pairs(comparison_csv))
    return samples


def load_comparison_pairs(
    comparison_csv: Path, since: Optional[datetime] = None
) -> List[PairedSample]:
    """OpenFlight-vs-reference sensor metrics from a ``compare_trackman.py`` CSV.

    Only rows whose ``match_quality`` is ``good`` are used. With ``since``,
    rows whose OpenFlight timestamp is missing or earlier are dropped.
    """
    sensor_fields = (
        ("ball_speed", "ball_speed_of", "ball_speed_tm"),
        ("launch_angle", "launch_v_of", "launch_v_tm"),
        ("spin", "spin_of", "spin_tm"),
        ("carry", "carry_of", "carry_tm"),
    )
    samples: List[PairedSample] = []
    # ComparisonRow carries no club speed, so it is read straight from the CSV.
    club_speeds = _club_speed_columns(comparison_csv)
    for comp, club_speed in zip(load_comparison(comparison_csv), club_speeds):
        if comp.match_quality != "good":
            continue
        if since is not None and not _is_on_or_after(comp.timestamp_of, since):
            continue
        club = _normalize_club(comp.club_raw)
        for metric, ours_attr, ref_attr in sensor_fields:
            ours = getattr(comp, ours_attr)
            reference = getattr(comp, ref_attr)
            if ours is None or reference is None:
                continue
            samples.append(PairedSample(metric, ours, reference, club))
        if club_speed is not None:
            samples.append(PairedSample("club_speed", club_speed[0], club_speed[1], club))
    return samples


def _is_on_or_after(timestamp: str, since: datetime) -> bool:
    try:
        shot_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if (shot_time.tzinfo is None) != (since.tzinfo is None):
        shot_time = shot_time.replace(tzinfo=since.tzinfo)
    return shot_time >= since


def _club_speed_columns(path: Path) -> List[Optional[tuple]]:
    """(club_speed_of, club_speed_tm) per comparison row, None when either is blank."""
    out: List[Optional[tuple]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            ours = _to_float(row.get("club_speed_of"))
            reference = _to_float(row.get("club_speed_tm"))
            out.append(None if ours is None or reference is None else (ours, reference))
    return out


@dataclass
class Report:
    stats: List[AgreementStats]
    checks: List[TargetCheck]
    envelope: List[TourEnvelopeRow]
    sources: List[str] = field(default_factory=list)

    @property
    def conclusive_checks(self) -> List[TargetCheck]:
        return [c for c in self.checks if c.status != "INCONCLUSIVE"]

    @property
    def pass_pct(self) -> float:
        conclusive = self.conclusive_checks
        if not conclusive:
            return float("nan")
        return 100.0 * sum(1 for c in conclusive if c.passed) / len(conclusive)

    def to_json(self) -> str:
        payload = {
            "sources": self.sources,
            "stats": [asdict(s) for s in self.stats],
            "targets": [asdict(c) for c in self.checks],
            "tour_envelope": [asdict(e) for e in self.envelope],
            "pass_pct": self.pass_pct,
        }
        return json.dumps(payload, indent=2, allow_nan=True)


def build_report(
    samples: Sequence[PairedSample],
    sources: Sequence[str] = (),
    air_density: float = AIR_DENSITY_STD,
) -> Report:
    stats = compute_stats(samples)
    return Report(
        stats=stats,
        checks=check_targets(stats),
        envelope=tour_envelope(air_density),
        sources=list(sources),
    )


def _fmt(value: float, width: int = 8, digits: int = 2) -> str:
    if math.isnan(value):
        return f"{'nan':>{width}s}"
    return f"{value:{width}.{digits}f}"


def format_report(report: Report) -> str:
    lines: List[str] = ["=== Accuracy report ==="]
    for source in report.sources:
        lines.append(f"source: {source}")
    lines.append("")
    lines.append(
        f"{'metric':14s} {'club':16s} {'n':>4s} {'bias':>8s} {'sd':>8s} "
        f"{'loa_low':>8s} {'loa_high':>8s} {'mae':>8s} {'rmse':>8s} {'icc':>6s} {'r':>6s}"
    )
    for s in report.stats:
        lines.append(
            f"{s.metric:14s} {s.club:16s} {s.n:4d} {_fmt(s.bias)} {_fmt(s.sd_diff)} "
            f"{_fmt(s.loa_low)} {_fmt(s.loa_high)} {_fmt(s.mae)} {_fmt(s.rmse)} "
            f"{_fmt(s.icc, 6, 3)} {_fmt(s.pearson_r, 6, 3)}"
        )
    lines.append("")
    lines.append("--- 95% confidence intervals (t-based) ---")
    lines.append(
        f"{'metric':14s} {'club':16s} {'n':>4s} {'bias_ci':>19s} "
        f"{'loa_low_ci':>19s} {'loa_high_ci':>19s}"
    )
    for s in report.stats:
        lines.append(
            f"{s.metric:14s} {s.club:16s} {s.n:4d} "
            f"[{_fmt(s.bias_ci_low)}, {_fmt(s.bias_ci_high)}] "
            f"[{_fmt(s.loa_low_ci_low)}, {_fmt(s.loa_low_ci_high)}] "
            f"[{_fmt(s.loa_high_ci_low)}, {_fmt(s.loa_high_ci_high)}]"
        )
    lines.append("")
    lines.append("--- Target bars ---")
    if not report.checks:
        lines.append("(no metric with a published target had >= 2 pairs)")
    for c in report.checks:
        note = f" (n={c.n} < {MIN_TARGET_N})" if c.status == "INCONCLUSIVE" else ""
        lines.append(
            f"{c.status:12s} {c.metric:12s} {c.club:12s} {c.stat:8s} "
            f"{_fmt(c.value, 9, 3)} {c.comparison} {c.threshold:<8g} [{c.source}]{note}"
        )
    if report.conclusive_checks:
        lines.append(f"targets passed: {report.pass_pct:.0f}%")
    elif report.checks:
        lines.append(f"targets passed: n/a (every target has fewer than {MIN_TARGET_N} pairs)")
    lines.append("")
    lines.append("--- Tour envelope (PGA Tour averages, TrackMan) ---")
    lines.append(
        f"{'club':8s} {'carry_ref':>9s} {'carry':>7s} {'err%':>6s} "
        f"{'apex_ref':>8s} {'apex':>6s} {'err%':>6s} {'land_ref':>8s} {'land':>6s} {'err%':>6s}"
    )
    for e in report.envelope:
        lines.append(
            f"{e.club:8s} {e.carry_ref_yd:9.0f} {e.carry_model_yd:7.1f} {e.carry_error_pct:+6.1f} "
            f"{e.apex_ref_yd:8.0f} {e.apex_model_yd:6.1f} {e.apex_error_pct:+6.1f} "
            f"{e.landing_ref_deg:8.0f} {e.landing_model_deg:6.1f} {e.landing_error_pct:+6.1f}"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--trackman", type=Path, help="TrackMan normalized CSV (model-only carry/apex metrics)."
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        help="Paired OF/TM session CSV from compare_trackman.py (sensor metrics).",
    )
    parser.add_argument(
        "--since",
        type=datetime.fromisoformat,
        help="Only score --comparison shots at or after this ISO date/time (e.g. 2026-10-01).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        action="append",
        default=[],
        help="Generic CSV with columns metric, ours, reference[, club]. Repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit 1 when fewer than this percentage of target bars pass.",
    )
    parser.add_argument(
        "--air-density",
        type=float,
        default=AIR_DENSITY_STD,
        help="Air density (kg/m^3) for the tour envelope simulation.",
    )
    args = parser.parse_args(argv)

    if args.since and not args.comparison:
        parser.error("--since requires --comparison")
    if not args.trackman and not args.comparison and not args.csv:
        parser.error("provide --trackman, --comparison and/or --csv")

    samples: List[PairedSample] = []
    sources: List[str] = []
    for path in (args.trackman, args.comparison):
        if path and not path.exists():
            print(f"CSV not found: {path}", file=sys.stderr)
            return 2
    if args.trackman:
        samples.extend(load_trackman_pairs(args.trackman))
        sources.append(str(args.trackman))
    if args.comparison:
        samples.extend(load_comparison_pairs(args.comparison, since=args.since))
        sources.append(str(args.comparison))
    for path in args.csv:
        if not path.exists():
            print(f"CSV not found: {path}", file=sys.stderr)
            return 2
        samples.extend(load_generic_csv(path))
        sources.append(str(path))

    report = build_report(samples, sources, air_density=args.air_density)
    if args.json:
        print(report.to_json())
    else:
        print(format_report(report))

    if args.fail_under is not None:
        if not report.conclusive_checks or report.pass_pct < args.fail_under:
            print(
                f"FAIL: {report.pass_pct:.0f}% of target bars passed "
                f"(required {args.fail_under:g}%)",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
