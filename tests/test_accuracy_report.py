"""Accuracy regression harness: statistics, loaders, targets and CLI."""

from __future__ import annotations

import json
import sys
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
            for i in range(6)
        ]
        samples += [
            harness.PairedSample("launch_angle", 10.0 + i, 13.0 + i * 0.5, "driver")
            for i in range(6)
        ]
        checks = harness.check_targets(harness.compute_stats(samples))
        by_key = {(c.metric, c.stat): c.passed for c in checks if c.club == "all"}
        assert by_key[("ball_speed", "abs_bias")] is True
        assert by_key[("ball_speed", "icc")] is True
        assert by_key[("launch_angle", "abs_bias")] is False

    def test_driver_only_spin_sd_bar_applies_per_club(self):
        driver = [harness.PairedSample("spin", 2500.0 + i * 30, 2500.0, "driver") for i in range(5)]
        wedge = [harness.PairedSample("spin", 9000.0 + i * 900, 9000.0, "pw") for i in range(5)]
        checks = harness.check_targets(harness.compute_stats(driver + wedge))
        sd_checks = {c.club: c.passed for c in checks if c.stat == "sd_diff"}
        assert sd_checks == {"driver": True}

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
        for club in ("driver", "7-iron", "pitching wedge"):
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
        lines = ["metric,ours,reference"] + [f"ball_speed,{100 + i},{110 + i}" for i in range(5)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert harness.main(["--csv", str(path), "--fail-under", "50"]) == 1
        assert "WARN" in capsys.readouterr().out
        assert harness.main(["--csv", str(path)]) == 0

    def test_json_output(self, tmp_path, capsys):
        path = tmp_path / "pairs.csv"
        path.write_text("metric,ours,reference\ncarry,200,201\ncarry,150,152\n", encoding="utf-8")
        assert harness.main(["--csv", str(path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["stats"][0]["metric"] == "carry"

    def test_missing_input_is_usage_error(self):
        with pytest.raises(SystemExit):
            harness.main([])
