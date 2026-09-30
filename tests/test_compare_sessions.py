"""Tests for scripts/analysis/compare_sessions.py on small synthetic inputs."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "analysis"))

import compare_sessions as cs  # noqa: E402  pylint: disable=wrong-import-position
import compare_trackman as ct  # noqa: E402  pylint: disable=wrong-import-position

T0 = datetime(2026, 5, 6, 15, 30, 0)


def _of_shot(**overrides) -> ct.Shot:
    values = {
        "source": "of",
        "shot_number": 1,
        "timestamp": T0,
        "club": "7-iron",
        "ball_speed_mph": 110.0,
        "club_speed_mph": 80.0,
        "launch_angle_vertical": 18.0,
        "spin_rpm": 6000.0,
        "carry_yards": 150.0,
    }
    values.update(overrides)
    return ct.Shot(**values)


def _ref_shot(**overrides) -> ct.Shot:
    values = {
        "source": "tm",
        "shot_number": 1,
        "timestamp": T0,
        "club": "7-iron",
        "ball_speed_mph": 112.0,
        "club_speed_mph": 84.0,
        "launch_angle_vertical": 19.0,
        "spin_rpm": 6500.0,
        "carry_yards": 155.0,
    }
    values.update(overrides)
    return ct.Shot(**values)


def _write_openflight_jsonl(path: Path, shots: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for shot in shots:
            entry = {"type": "shot_detected", "ts": shot.pop("ts"), "data": shot}
            fh.write(json.dumps(entry) + "\n")


def _write_reference_csv(path: Path, rows: list[dict]) -> None:
    headers = [
        "Date",
        "Club",
        "Ball Speed (mph)",
        "Club Speed (mph)",
        "Launch Angle",
        "Spin Rate",
        "Carry Distance",
    ]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class TestSummaries:
    def test_empty_metric_reports_no_values(self):
        assert cs.summarize_errors([]) == {"n": 0, "bias": None, "mae": None}

    def test_bias_is_signed_and_mae_is_absolute(self):
        stats = cs.summarize_errors([-2.0, -4.0, +3.0])
        assert stats["n"] == 3
        assert stats["bias"] == pytest.approx(-1.0)
        assert stats["mae"] == pytest.approx(3.0)

    def test_metric_errors_skip_mismatched_and_incomplete_pairs(self):
        pairs = [
            ct.Pair(of=_of_shot(), tm=_ref_shot(), match_quality="good"),
            ct.Pair(
                of=_of_shot(ball_speed_mph=90.0),
                tm=_ref_shot(),
                match_quality="ball_speed_mismatch",
            ),
            ct.Pair(of=_of_shot(spin_rpm=None), tm=_ref_shot(), match_quality="good"),
            ct.Pair(of=_of_shot(), tm=None, match_quality="unmatched_openflight"),
        ]
        assert cs.metric_errors(pairs, "ball_speed_mph") == [-2.0, -2.0]
        assert cs.metric_errors(pairs, "spin_rpm") == [-500.0]


class TestOrderPairingReport:
    def test_per_club_and_overall_bias_mae(self):
        of_shots = [
            _of_shot(shot_number=1, timestamp=T0, ball_speed_mph=110.0, carry_yards=150.0),
            _of_shot(
                shot_number=2,
                timestamp=T0 + timedelta(minutes=1),
                ball_speed_mph=108.0,
                carry_yards=140.0,
            ),
            _of_shot(
                shot_number=3,
                timestamp=T0 + timedelta(minutes=2),
                club="driver",
                ball_speed_mph=150.0,
                club_speed_mph=100.0,
                launch_angle_vertical=12.0,
                spin_rpm=None,
                carry_yards=240.0,
            ),
        ]
        ref_shots = [
            _ref_shot(shot_number=1, timestamp=T0, ball_speed_mph=112.0, carry_yards=155.0),
            _ref_shot(
                shot_number=2,
                timestamp=T0 + timedelta(minutes=1),
                ball_speed_mph=112.0,
                carry_yards=141.0,
            ),
            _ref_shot(
                shot_number=3,
                timestamp=T0 + timedelta(minutes=2),
                club="driver",
                ball_speed_mph=153.0,
                club_speed_mph=104.0,
                launch_angle_vertical=11.0,
                spin_rpm=2500.0,
                carry_yards=250.0,
            ),
        ]

        pairs = cs.build_pairs(
            of_shots,
            ref_shots,
            pair_by="order",
            ball_speed_tol_mph=5.0,
            time_tolerance_s=30.0,
            time_offset_s=0.0,
            club_filter=None,
        )
        report = cs.build_report(pairs, openflight_shots=3, reference_shots=3, pairing="order")

        assert report["counts"] == {
            "good": 3,
            "ball_speed_mismatch": 0,
            "unmatched_openflight": 0,
            "unmatched_trackman": 0,
            "pairs": 3,
        }
        iron = report["clubs"]["7-iron"]
        assert iron["pairs"] == 2 and iron["good"] == 2
        assert iron["metrics"]["ball_speed_mph"]["bias"] == pytest.approx(-3.0)
        assert iron["metrics"]["ball_speed_mph"]["mae"] == pytest.approx(3.0)
        assert iron["metrics"]["carry_yards"]["bias"] == pytest.approx(-3.0)
        assert iron["metrics"]["carry_yards"]["mae"] == pytest.approx(3.0)
        assert iron["metrics"]["spin_rpm"]["bias"] == pytest.approx(-500.0)

        driver = report["clubs"]["driver"]
        assert driver["metrics"]["ball_speed_mph"]["bias"] == pytest.approx(-3.0)
        assert driver["metrics"]["launch_angle_vertical"]["bias"] == pytest.approx(1.0)
        assert driver["metrics"]["spin_rpm"] == {"n": 0, "bias": None, "mae": None}

        overall = report["overall"]["metrics"]
        assert overall["ball_speed_mph"]["n"] == 3
        assert overall["ball_speed_mph"]["bias"] == pytest.approx(-3.0)
        assert overall["club_speed_mph"]["bias"] == pytest.approx(-4.0)
        assert overall["spin_rpm"]["n"] == 2

    def test_mismatched_pair_is_counted_but_excluded_from_stats(self):
        pairs = cs.build_pairs(
            [
                _of_shot(ball_speed_mph=90.0),
                _of_shot(shot_number=2, timestamp=T0 + timedelta(minutes=1)),
            ],
            [_ref_shot(), _ref_shot(shot_number=2, timestamp=T0 + timedelta(minutes=1))],
            pair_by="order",
            ball_speed_tol_mph=5.0,
            time_tolerance_s=30.0,
            time_offset_s=0.0,
            club_filter=None,
        )
        report = cs.build_report(pairs, openflight_shots=2, reference_shots=2, pairing="order")
        assert report["counts"]["ball_speed_mismatch"] == 1
        assert report["counts"]["good"] == 1
        assert report["overall"]["metrics"]["ball_speed_mph"]["n"] == 1
        assert report["overall"]["metrics"]["ball_speed_mph"]["bias"] == pytest.approx(-2.0)


class TestTimestampPairing:
    def test_nearest_timestamp_wins_within_tolerance(self):
        of_shots = [
            _of_shot(shot_number=1, timestamp=T0 + timedelta(seconds=3)),
            _of_shot(shot_number=2, timestamp=T0 + timedelta(seconds=63), ball_speed_mph=109.0),
            _of_shot(shot_number=3, timestamp=T0 + timedelta(seconds=600)),
        ]
        ref_shots = [
            _ref_shot(shot_number=1, timestamp=T0),
            _ref_shot(shot_number=2, timestamp=T0 + timedelta(seconds=60)),
            _ref_shot(shot_number=9, timestamp=T0 + timedelta(seconds=300)),
        ]

        pairs = cs.pair_by_timestamp(of_shots, ref_shots, tolerance_s=10.0)

        matched = [(p.of.shot_number, p.tm.shot_number) for p in pairs if p.of and p.tm]
        assert matched == [(1, 1), (2, 2)]
        assert all(p.match_quality == "good" for p in pairs if p.of and p.tm)
        assert [p.of.shot_number for p in pairs if p.match_quality == "unmatched_openflight"] == [3]
        assert [p.tm.shot_number for p in pairs if p.match_quality == "unmatched_trackman"] == [9]
        assert "dt 3.0s" in pairs[0].notes

    def test_offset_absorbs_constant_clock_skew(self):
        of_shots = [_of_shot(timestamp=T0 + timedelta(seconds=120))]
        ref_shots = [_ref_shot(timestamp=T0)]

        assert all(
            p.match_quality.startswith("unmatched")
            for p in cs.pair_by_timestamp(of_shots, ref_shots, tolerance_s=5.0)
        )
        pairs = cs.pair_by_timestamp(of_shots, ref_shots, tolerance_s=5.0, offset_s=120.0)
        assert [p.match_quality for p in pairs] == ["good"]

    def test_club_disagreement_is_noted_and_ball_speed_tol_applies(self):
        of_shots = [_of_shot(club="driver", ball_speed_mph=150.0)]
        ref_shots = [_ref_shot(club="7-iron", ball_speed_mph=112.0)]

        pairs = cs.pair_by_timestamp(of_shots, ref_shots, tolerance_s=10.0, ball_speed_tol_mph=5.0)

        assert len(pairs) == 1
        assert pairs[0].match_quality == "ball_speed_mismatch"
        assert "club differs (driver vs 7-iron)" in pairs[0].notes

    def test_shots_without_timestamps_are_unmatched(self):
        pairs = cs.pair_by_timestamp(
            [_of_shot(timestamp=None)], [_ref_shot(timestamp=None)], tolerance_s=10.0
        )
        assert sorted(p.match_quality for p in pairs) == [
            "unmatched_openflight",
            "unmatched_trackman",
        ]

    def test_report_groups_timestamp_pairs_by_openflight_club(self):
        pairs = cs.pair_by_timestamp(
            [_of_shot(club="driver", ball_speed_mph=150.0)],
            [_ref_shot(club="7-iron", ball_speed_mph=151.0)],
            tolerance_s=10.0,
        )
        report = cs.build_report(pairs, openflight_shots=1, reference_shots=1, pairing="timestamp")
        assert list(report["clubs"]) == ["driver"]
        assert report["clubs"]["driver"]["metrics"]["ball_speed_mph"]["bias"] == pytest.approx(-1.0)


class TestCli:
    def _write_inputs(self, tmp_path: Path) -> tuple[Path, Path]:
        of_path = tmp_path / "session.jsonl"
        ref_path = tmp_path / "reference.csv"
        _write_openflight_jsonl(
            of_path,
            [
                {
                    "ts": "2026-05-06T15:30:00",
                    "shot_number": 1,
                    "club": "7-iron",
                    "ball_speed_mph": 110.0,
                    "club_speed_mph": 80.0,
                    "launch_angle_vertical": 18.0,
                    "spin_rpm": 6000.0,
                    "carry_spin_adjusted": 150.0,
                },
                {
                    "ts": "2026-05-06T15:31:00",
                    "shot_number": 2,
                    "club": "driver",
                    "ball_speed_mph": 150.0,
                    "club_speed_mph": 100.0,
                    "launch_angle_vertical": 12.0,
                    "spin_rpm": None,
                    "carry_spin_adjusted": 240.0,
                },
            ],
        )
        _write_reference_csv(
            ref_path,
            [
                {
                    "Date": "2026-05-06 15:30:02",
                    "Club": "7 Iron",
                    "Ball Speed (mph)": "112.0",
                    "Club Speed (mph)": "84.0",
                    "Launch Angle": "19.0",
                    "Spin Rate": "6500",
                    "Carry Distance": "155.0",
                },
                {
                    "Date": "2026-05-06 15:31:01",
                    "Club": "Driver",
                    "Ball Speed (mph)": "153.0",
                    "Club Speed (mph)": "104.0",
                    "Launch Angle": "11.0",
                    "Spin Rate": "2500",
                    "Carry Distance": "250.0",
                },
            ],
        )
        return of_path, ref_path

    def test_text_report_lists_clubs_and_overall(self, tmp_path, capsys):
        of_path, ref_path = self._write_inputs(tmp_path)

        assert cs.main(["--openflight", str(of_path), "--reference", str(ref_path)]) == 0

        out = capsys.readouterr().out
        assert "OpenFlight shots:       2" in out
        assert "Reference shots:        2" in out
        assert "Good:                   2" in out
        assert "7-iron - 1 good pair(s) of 1" in out
        assert "driver - 1 good pair(s) of 1" in out
        assert "ALL CLUBS - 2 good pair(s) of 2" in out
        assert "ball speed         -2.50        2.50  mph (n=2)" in out

    @pytest.mark.parametrize("pair_by", ["order", "timestamp"])
    def test_json_report_matches_build_report(self, tmp_path, capsys, pair_by):
        of_path, ref_path = self._write_inputs(tmp_path)

        assert (
            cs.main(
                [
                    "--openflight",
                    str(of_path),
                    "--trackman",
                    str(ref_path),
                    "--pair-by",
                    pair_by,
                    "--json",
                ]
            )
            == 0
        )

        report = json.loads(capsys.readouterr().out)
        assert report["pairing"] == pair_by
        assert report["counts"]["good"] == 2
        assert report["clubs"]["7-iron"]["metrics"]["ball_speed_mph"]["bias"] == pytest.approx(-2.0)
        assert report["clubs"]["7-iron"]["metrics"]["spin_rpm"]["bias"] == pytest.approx(-500.0)
        assert report["clubs"]["driver"]["metrics"]["carry_yards"]["mae"] == pytest.approx(10.0)
        assert report["clubs"]["driver"]["metrics"]["spin_rpm"]["n"] == 0
        assert report["overall"]["metrics"]["club_speed_mph"]["bias"] == pytest.approx(-4.0)
        assert report["overall"]["metrics"]["launch_angle_vertical"]["bias"] == pytest.approx(0.0)
        assert report["overall"]["metrics"]["launch_angle_vertical"]["mae"] == pytest.approx(1.0)

    def test_club_filter_limits_both_pairing_modes(self, tmp_path, capsys):
        of_path, ref_path = self._write_inputs(tmp_path)
        for pair_by in ("order", "timestamp"):
            args = [
                "--openflight",
                str(of_path),
                "--reference",
                str(ref_path),
                "--pair-by",
                pair_by,
                "--club-filter",
                "driver",
                "--json",
            ]
            assert cs.main(args) == 0
            report = json.loads(capsys.readouterr().out)
            assert list(report["clubs"]) == ["driver"]

    def test_timestamp_mode_warns_when_reference_has_no_timestamps(self, tmp_path, capsys):
        of_path, ref_path = self._write_inputs(tmp_path)
        _write_reference_csv(
            ref_path,
            [{"Date": "", "Club": "7 Iron", "Ball Speed (mph)": "112.0"}],
        )

        assert (
            cs.main(
                [
                    "--openflight",
                    str(of_path),
                    "--reference",
                    str(ref_path),
                    "--pair-by",
                    "timestamp",
                ]
            )
            == 0
        )

        captured = capsys.readouterr()
        assert "no reference shot has a timestamp" in captured.err
        assert "Unmatched reference:    1" in captured.out

    def test_missing_inputs_return_2(self, tmp_path, capsys):
        of_path, ref_path = self._write_inputs(tmp_path)
        assert (
            cs.main(["--openflight", str(tmp_path / "nope.jsonl"), "--reference", str(ref_path)])
            == 2
        )
        assert (
            cs.main(["--openflight", str(of_path), "--reference", str(tmp_path / "nope.csv")]) == 2
        )
        assert "not found" in capsys.readouterr().err
