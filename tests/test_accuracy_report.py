"""Accuracy regression harness: statistics, loaders, targets and CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

import accuracy_report as harness  # noqa: E402
from validate_ballistics import _stats, load_trackman, validate_tm_inputs  # noqa: E402

TRACKMAN_CSV = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"
COMPARISON_CSV = _REPO_ROOT / "session_logs" / "comparison_20260506.csv"

# Shrout & Fleiss (1979), Table 2: six targets rated by four judges.
# Published ICC(2,1) = 0.29.
SHROUT_FLEISS = [
    [9, 2, 5, 8],
    [6, 1, 3, 2],
    [8, 4, 6, 8],
    [7, 1, 2, 6],
    [10, 5, 6, 9],
    [6, 2, 4, 7],
]


class TestIcc:
    def test_hand_computed_two_rater_example(self):
        # ours = reference + 1 for four subjects. By hand: MSR = 10/3,
        # MSC = 2, MSE = 0 -> ICC = (10/3) / (10/3 + 2 * 2 / 4) = 0.76923.
        ratings = [[2, 1], [3, 2], [4, 3], [5, 4]]
        assert harness.icc_2_1(ratings) == pytest.approx(10 / 13, abs=1e-9)

    def test_perfect_agreement_is_one(self):
        assert harness.icc_2_1([[1, 1], [2, 2], [3, 3]]) == pytest.approx(1.0)

    def test_published_shrout_fleiss_value(self):
        assert harness.icc_2_1(SHROUT_FLEISS) == pytest.approx(0.29, abs=0.005)

    def test_degenerate_inputs_are_nan(self):
        assert harness.icc_2_1([[1, 1]]) != harness.icc_2_1([[1, 1]])  # NaN
        assert harness.icc_2_1([[1, 1], [1, 1]]) != harness.icc_2_1([[1, 1], [1, 1]])


class TestAgreementStats:
    def test_bland_altman_and_error_metrics(self):
        ours = [10.0, 12.0, 14.0, 16.0]
        reference = [9.0, 12.0, 13.0, 18.0]
        stats = harness.agreement_stats(ours, reference, metric="m", club="c")
        # diffs = [1, 0, 1, -2]: mean 0, sample SD sqrt(6/3) = sqrt(2)
        assert stats.n == 4
        assert stats.bias == pytest.approx(0.0)
        assert stats.sd_diff == pytest.approx(2**0.5)
        assert stats.loa_low == pytest.approx(-1.96 * 2**0.5)
        assert stats.loa_high == pytest.approx(1.96 * 2**0.5)
        assert stats.mae == pytest.approx(1.0)
        assert stats.rmse == pytest.approx((6 / 4) ** 0.5)
        assert 0.0 < stats.pearson_r <= 1.0
        assert stats.icc == pytest.approx(harness.icc_2_1(list(zip(ours, reference))))

    def test_t_based_confidence_intervals(self):
        stats = harness.agreement_stats([10.0, 12.0, 14.0, 16.0], [9.0, 12.0, 13.0, 18.0])
        # n = 4, SD = sqrt(2), t(0.975, 3) = 3.18245.
        # bias half-width = t * SD / sqrt(4) = 2.25033
        # LoA half-width = t * SD * sqrt(1/4 + 1.96^2 / 6) = 4.24655
        loa = 1.96 * 2**0.5
        assert stats.bias_ci_low == pytest.approx(-2.2503294, abs=1e-6)
        assert stats.bias_ci_high == pytest.approx(2.2503294, abs=1e-6)
        assert stats.loa_low_ci_low == pytest.approx(-loa - 4.2465490, abs=1e-6)
        assert stats.loa_low_ci_high == pytest.approx(-loa + 4.2465490, abs=1e-6)
        assert stats.loa_high_ci_low == pytest.approx(loa - 4.2465490, abs=1e-6)
        assert stats.loa_high_ci_high == pytest.approx(loa + 4.2465490, abs=1e-6)

    def test_confidence_intervals_need_two_pairs(self):
        single = harness.agreement_stats([1.0], [3.0])
        assert single.bias_ci_low != single.bias_ci_low
        assert single.loa_high_ci_high != single.loa_high_ci_high

    def test_empty_and_single_pair(self):
        empty = harness.agreement_stats([], [])
        assert empty.n == 0 and empty.bias != empty.bias
        single = harness.agreement_stats([1.0], [3.0])
        assert single.n == 1 and single.bias == -2.0 and single.icc != single.icc

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError):
            harness.agreement_stats([1.0, 2.0], [1.0])


class TestTargets:
    def test_pass_and_warn_per_metric(self):
        samples = [
            harness.PairedSample("ball_speed", 100.0 + i * 5, 100.5 + i * 5, "driver")
            for i in range(20)
        ]
        samples += [
            harness.PairedSample("launch_angle", 10.0 + i, 13.0 + i * 0.5, "driver")
            for i in range(20)
        ]
        checks = harness.check_targets(harness.compute_stats(samples))
        by_key = {(c.metric, c.stat): c.passed for c in checks if c.club == "all"}
        assert by_key[("ball_speed", "abs_bias")] is True
        assert by_key[("ball_speed", "icc")] is True
        assert by_key[("launch_angle", "abs_bias")] is False

    def test_driver_only_spin_sd_bar_applies_per_club(self):
        driver = [
            harness.PairedSample("spin", 2500.0 + i * 30, 2500.0, "driver") for i in range(20)
        ]
        wedge = [harness.PairedSample("spin", 9000.0 + i * 900, 9000.0, "pw") for i in range(20)]
        checks = harness.check_targets(harness.compute_stats(driver + wedge))
        sd_checks = {c.club: c.passed for c in checks if c.stat == "sd_diff"}
        assert sd_checks == {"driver": True}

    def test_fewer_than_twenty_pairs_are_inconclusive(self):
        def checks_for(n):
            samples = [harness.PairedSample("carry", 100.0 + i, 100.0 + i) for i in range(n)]
            return harness.check_targets(harness.compute_stats(samples))

        assert {c.status for c in checks_for(19)} == {"INCONCLUSIVE"}
        assert {c.status for c in checks_for(20)} == {"PASS"}

    def test_inconclusive_checks_are_left_out_of_pass_pct(self):
        samples = [harness.PairedSample("carry", 100.0 + i, 100.0 + i) for i in range(20)]
        samples += [harness.PairedSample("ball_speed", 100.0 + i, 110.0 + i) for i in range(5)]
        report = harness.build_report(samples)
        assert {c.metric: c.status for c in report.checks} == {
            "carry": "PASS",
            "ball_speed": "INCONCLUSIVE",
        }
        assert report.pass_pct == pytest.approx(100.0)
        text = harness.format_report(report)
        assert "INCONCLUSIVE" in text and "(n=5 < 20)" in text
        assert "95% confidence intervals" in text

    def test_fewer_than_two_pairs_are_not_judged(self):
        checks = harness.check_targets(
            harness.compute_stats([harness.PairedSample("ball_speed", 1.0, 2.0)])
        )
        assert checks == []


class TestGenericCsv:
    def test_loads_rows_and_skips_blanks(self, tmp_path):
        path = tmp_path / "pairs.csv"
        path.write_text(
            "metric,ours,reference,club\n"
            "Ball_Speed,150.1,151.0,Driver\n"
            "ball_speed,,151.0,driver\n"
            "spin,2500,2600,\n",
            encoding="utf-8",
        )
        samples = harness.load_generic_csv(path)
        assert [(s.metric, s.ours, s.reference, s.club) for s in samples] == [
            ("ball_speed", 150.1, 151.0, "driver"),
            ("spin", 2500.0, 2600.0, ""),
        ]

    def test_club_name_spellings_share_one_row(self, tmp_path):
        path = tmp_path / "pairs.csv"
        path.write_text(
            "metric,ours,reference,club\n"
            "carry,120,121,Pitching Wedge\n"
            "carry,118,119,PW\n"
            "carry,119,121,pitching-wedge\n",
            encoding="utf-8",
        )
        stats = harness.compute_stats(harness.load_generic_csv(path))
        assert [(s.club, s.n) for s in stats] == [("all", 3), ("pw", 3)]

    def test_missing_column_is_an_error(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("metric,ours\nball_speed,1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="reference"):
            harness.load_generic_csv(path)


@pytest.fixture(scope="module")
def committed_report():
    if not TRACKMAN_CSV.exists() or not COMPARISON_CSV.exists():
        pytest.skip("committed TrackMan capture not present")
    samples = harness.load_trackman_pairs(TRACKMAN_CSV, COMPARISON_CSV)
    return harness.build_report(samples, [str(TRACKMAN_CSV), str(COMPARISON_CSV)])


class TestCommittedCapture:
    def test_carry_model_rmse_matches_trackman_regression_test(self, committed_report):
        """Same loaders as test_ballistics_trackman_regression -> identical RMSEs."""
        rows = validate_tm_inputs(load_trackman(TRACKMAN_CSV))
        expected_overall = _stats([r.delta_yards for r in rows])["rmse"]
        by_club = {s.club: s for s in committed_report.stats if s.metric == "carry_model"}
        assert by_club["all"].n == len(rows) == 24
        assert by_club["all"].rmse == pytest.approx(expected_overall, abs=1e-9)
        for club in ("driver", "7-iron", "pw"):
            expected = _stats([r.delta_yards for r in rows if r.club == club])["rmse"]
            assert by_club[club].rmse == pytest.approx(expected, abs=1e-9), club
        apex = {s.club: s for s in committed_report.stats if s.metric == "apex_model"}
        expected_apex = _stats([r.delta_apex_feet for r in rows])["rmse"]
        assert apex["all"].rmse == pytest.approx(expected_apex, abs=1e-9)

    def test_sensor_metrics_come_from_comparison_rows(self, committed_report):
        metrics = {s.metric for s in committed_report.stats}
        assert {"ball_speed", "club_speed", "launch_angle", "carry", "spin"} <= metrics
        ball = next(
            s for s in committed_report.stats if s.metric == "ball_speed" and s.club == "all"
        )
        assert ball.n == 22

    def test_tour_envelope_is_within_model_tolerance(self, committed_report):
        clubs = {row.club for row in committed_report.envelope}
        assert clubs == {"driver", "7-iron", "pw"}
        for row in committed_report.envelope:
            assert abs(row.carry_error_pct) < 8.0, row
            assert abs(row.apex_error_pct) < 10.0, row
            assert abs(row.landing_error_pct) < 12.0, row

    def test_json_round_trips(self, committed_report):
        payload = json.loads(committed_report.to_json())
        assert {"stats", "targets", "tour_envelope", "pass_pct", "sources"} <= set(payload)
        assert len(payload["tour_envelope"]) == 3


class TestCli:
    def test_fail_under_exit_code(self, tmp_path, capsys):
        path = tmp_path / "pairs.csv"
        lines = ["metric,ours,reference"] + [f"ball_speed,{100 + i},{110 + i}" for i in range(20)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert harness.main(["--csv", str(path), "--fail-under", "50"]) == 1
        assert "WARN" in capsys.readouterr().out
        assert harness.main(["--csv", str(path)]) == 0

    def test_fail_under_fails_when_every_target_is_inconclusive(self, tmp_path):
        path = tmp_path / "pairs.csv"
        lines = ["metric,ours,reference"] + [f"carry,{100 + i},{100 + i}" for i in range(5)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert harness.main(["--csv", str(path), "--fail-under", "50"]) == 1

    def test_json_output(self, tmp_path, capsys):
        path = tmp_path / "pairs.csv"
        path.write_text("metric,ours,reference\ncarry,200,201\ncarry,150,152\n", encoding="utf-8")
        assert harness.main(["--csv", str(path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["stats"][0]["metric"] == "carry"
        assert {"bias_ci_low", "loa_low_ci_low", "loa_high_ci_high"} <= set(payload["stats"][0])
        assert payload["targets"][0]["status"] == "INCONCLUSIVE"

    def test_missing_input_is_usage_error(self):
        with pytest.raises(SystemExit):
            harness.main([])


SESSION_HEADER = (
    "timestamp_of,club,ball_speed_of,ball_speed_tm,club_speed_of,club_speed_tm,"
    "launch_v_of,launch_v_tm,spin_of,spin_tm,carry_of,carry_tm,match_quality\n"
)


def _write_session(tmp_path: Path) -> Path:
    path = tmp_path / "comparison_new.csv"
    path.write_text(
        SESSION_HEADER
        + "2026-09-30T18:00:00,Driver,150,152,100,101,11,12,2500,2600,240,245,good\n"
        + "2026-10-01T09:00:00,Pitching Wedge,90,91,75,76,24,25,8500,8800,115,118,good\n"
        + "2026-10-01T09:01:00,pw,92,93,76,77,23,24,8600,8700,117,119,good\n"
        + "2026-10-01T09:02:00,pw,60,92,,,,,,,,,ball_speed_mismatch\n"
        + ",pw,93,94,,,,,,,,,good\n",
        encoding="utf-8",
    )
    return path


class TestComparisonSession:
    def test_scores_a_session_file_without_trackman(self, tmp_path, capsys):
        path = _write_session(tmp_path)
        assert harness.main(["--comparison", str(path), "--json"]) == 0
        stats = json.loads(capsys.readouterr().out)["stats"]
        ball = {s["club"]: s["n"] for s in stats if s["metric"] == "ball_speed"}
        assert ball == {"all": 4, "driver": 1, "pw": 3}
        assert not any(s["metric"] == "carry_model" for s in stats)

    def test_since_keeps_only_later_timestamped_shots(self, tmp_path):
        path = _write_session(tmp_path)
        samples = harness.load_comparison_pairs(path, since=datetime(2026, 10, 1))
        ball = [s for s in samples if s.metric == "ball_speed"]
        assert [(s.ours, s.club) for s in ball] == [(90.0, "pw"), (92.0, "pw")]

    def test_since_accepts_a_timezone_aware_cutoff(self, tmp_path):
        path = _write_session(tmp_path)
        since = datetime(2026, 10, 1, 9, 0, 30, tzinfo=timezone.utc)
        ball = [s for s in harness.load_comparison_pairs(path, since=since) if s.metric == "ball_speed"]
        assert [s.ours for s in ball] == [92.0]

    def test_without_since_every_good_row_is_scored(self, tmp_path):
        ball = [
            s for s in harness.load_comparison_pairs(_write_session(tmp_path)) if s.metric == "ball_speed"
        ]
        assert len(ball) == 4

    def test_since_requires_comparison(self, tmp_path):
        path = tmp_path / "pairs.csv"
        path.write_text("metric,ours,reference\ncarry,1,2\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            harness.main(["--csv", str(path), "--since", "2026-10-01"])

    def test_missing_session_file_is_an_input_error(self, tmp_path):
        assert harness.main(["--comparison", str(tmp_path / "missing.csv")]) == 2
