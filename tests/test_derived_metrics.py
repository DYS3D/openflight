"""Derived display metrics (--derived-metrics): pure module and server payload wiring."""

import math
import sys
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ballistics import Trajectory, resolve_launch, simulate
from openflight.clubs import ClubType
from openflight.derived_metrics import derive, format_for_log, shot_shape
from openflight.launch_monitor import Shot
from openflight.spin_axis import dplane_club_parameters

from .test_ballistic_display import _finalize, _live_shot, emitted  # noqa: F401

CONTRACT_KEYS = {
    "smash_factor",
    "face_to_path_deg",
    "face_angle_deg",
    "spin_loft_deg",
    "dynamic_loft_deg",
    "curve_yards",
    "side_yards",
    "apex_yards",
    "hang_time_s",
    "landing_angle_deg",
    "descent_speed_mph",
    "roll_yards",
    "total_yards",
    "shot_shape",
}


def _trajectory(**overrides) -> Trajectory:
    values = {
        "points": [],
        "carry_yards": 250.0,
        "apex_yards": 32.0,
        "lateral_yards": 8.0,
        "flight_time_s": 6.2,
        "landing_speed_mph": 60.0,
        "landing_angle_deg": 40.0,
    }
    values.update(overrides)
    return Trajectory(**values)


class TestShotShape:
    @pytest.mark.parametrize(
        "hla, curve, expected",
        [
            (0.0, 0.0, "straight"),
            (0.0, 3.0, "straight"),
            (0.0, 3.1, "fade"),
            (0.0, 15.1, "slice"),
            (0.0, -3.1, "draw"),
            (0.0, -15.1, "hook"),
            (2.1, 0.0, "push"),
            (-2.1, 0.0, "pull"),
            (2.1, 4.0, "push-fade"),
            (2.1, -4.0, "push-draw"),
            (-2.1, 4.0, "pull-fade"),
            (-2.1, -4.0, "pull-draw"),
            (2.0, -3.0, "straight"),
        ],
    )
    def test_thresholds(self, hla, curve, expected):
        assert shot_shape(hla, curve) == expected


class TestDerive:
    def test_only_contract_keys_and_every_entry_labelled(self):
        shot = _live_shot(club_path_deg=-1.0, club_angle_deg=2.0)
        derived = derive(shot, _trajectory())

        assert set(derived) == CONTRACT_KEYS
        for key, entry in derived.items():
            assert set(entry) == {"value", "source"}
            assert entry["source"] in ("measured", "estimated")
            if key == "shot_shape":
                assert isinstance(entry["value"], str)
            else:
                assert isinstance(entry["value"], float)

    def test_smash_factor_is_measured_from_radar_speeds(self):
        derived = derive(_live_shot())
        assert derived["smash_factor"] == {"value": pytest.approx(150.0 / 102.0), "source": "measured"}

    def test_smash_factor_estimated_for_mock_and_absent_without_club_speed(self):
        assert derive(_live_shot(mode="mock"))["smash_factor"]["source"] == "estimated"
        assert "smash_factor" not in derive(_live_shot(club_speed_mph=None))

    def test_face_angle_is_start_direction_without_club_path(self):
        derived = derive(_live_shot(launch_angle_horizontal=2.0))
        assert derived["face_angle_deg"] == {"value": 2.0, "source": "estimated"}
        assert "face_to_path_deg" not in derived

    def test_face_angle_uses_dplane_start_direction_ratio_with_club_path(self):
        shot = _live_shot(launch_angle_horizontal=2.0, club_path_deg=-3.0, club=ClubType.DRIVER)
        derived = derive(shot)
        k = dplane_club_parameters(ClubType.DRIVER).face_weight
        expected_face = -3.0 + (2.0 - -3.0) / k
        assert derived["face_angle_deg"]["value"] == pytest.approx(expected_face)
        assert derived["face_to_path_deg"]["value"] == pytest.approx(expected_face - -3.0)
        assert derived["face_to_path_deg"]["source"] == "estimated"

    def test_loft_uses_club_prior_attack_angle_when_unmeasured(self):
        shot = _live_shot(launch_angle_vertical=12.0, club=ClubType.DRIVER)
        derived = derive(shot)
        params = dplane_club_parameters(ClubType.DRIVER)
        dynamic_loft = 12.0 / params.launch_ratio
        assert derived["dynamic_loft_deg"]["value"] == pytest.approx(dynamic_loft)
        assert derived["spin_loft_deg"]["value"] == pytest.approx(
            dynamic_loft - params.attack_angle_deg
        )

    def test_loft_uses_measured_attack_angle_when_present(self):
        shot = _live_shot(launch_angle_vertical=12.0, club_angle_deg=3.0, club=ClubType.DRIVER)
        derived = derive(shot)
        dynamic_loft = 12.0 / dplane_club_parameters(ClubType.DRIVER).launch_ratio
        assert derived["spin_loft_deg"]["value"] == pytest.approx(dynamic_loft - 3.0)

    def test_angle_keys_absent_without_launch_angles(self):
        derived = derive(_live_shot(launch_angle_vertical=None, launch_angle_horizontal=None))
        assert set(derived) == {"smash_factor"}

    def test_flight_metrics_come_from_the_trajectory(self):
        trajectory = _trajectory()
        derived = derive(_live_shot(launch_angle_horizontal=0.0), trajectory)
        assert derived["apex_yards"]["value"] == 32.0
        assert derived["hang_time_s"]["value"] == 6.2
        assert derived["landing_angle_deg"]["value"] == 40.0
        assert derived["descent_speed_mph"]["value"] == 60.0
        assert derived["side_yards"]["value"] == 8.0
        assert derived["total_yards"]["value"] == pytest.approx(trajectory.total_yards)
        assert derived["roll_yards"]["value"] == pytest.approx(trajectory.total_yards - 250.0)
        assert derived["curve_yards"]["value"] == pytest.approx(8.0)
        assert derived["shot_shape"] == {"value": "fade", "source": "estimated"}

    def test_curve_removes_the_straight_line_start_direction(self):
        hla = 2.0
        straight_side = 250.0 * math.tan(math.radians(hla))
        derived = derive(
            _live_shot(launch_angle_horizontal=hla), _trajectory(lateral_yards=straight_side)
        )
        assert derived["curve_yards"]["value"] == pytest.approx(0.0, abs=1e-9)
        assert derived["shot_shape"]["value"] == "straight"

    def test_flight_keys_absent_without_trajectory_and_curve_absent_without_hla(self):
        derived = derive(_live_shot())
        assert not CONTRACT_KEYS.intersection(derived) - {
            "smash_factor",
            "face_angle_deg",
            "dynamic_loft_deg",
            "spin_loft_deg",
        }
        derived = derive(_live_shot(launch_angle_horizontal=None), _trajectory())
        assert "side_yards" in derived
        assert "curve_yards" not in derived
        assert "shot_shape" not in derived

    def test_format_for_log_lists_every_metric(self):
        text = format_for_log(derive(_live_shot(launch_angle_horizontal=0.0), _trajectory()))
        assert "smash_factor=1.5" in text
        assert "shot_shape=fade" in text


class TestServerPayload:
    def test_off_by_default_key_absent(self, emitted):
        assert server_module.derived_metrics_enabled is False
        shot = _live_shot()

        payload = _finalize(shot)

        assert shot.derived is None
        assert "derived" not in payload
        assert "derived" not in shot.to_dict()
        assert all(
            "derived" not in p["shot"] for e, p in emitted if e in ("shot", "shot_update")
        )

    def test_on_attaches_rounded_block_to_ui_payload_only(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        shot = _live_shot(club_path_deg=-1.0)

        payload = _finalize(shot)

        assert "derived" not in shot.to_dict()
        derived = payload["derived"]
        assert set(derived) == CONTRACT_KEYS
        trajectory = simulate(resolve_launch(shot), air_density=server_module.air_density)
        assert derived["apex_yards"]["value"] == round(trajectory.apex_yards, 2)
        assert derived["smash_factor"] == {"value": 1.47, "source": "measured"}
        assert derived["shot_shape"]["source"] == "estimated"
        final_events = [p["shot"] for e, p in emitted if e in ("shot", "shot_update")]
        assert final_events[-1]["derived"] == derived

    def test_on_without_ballistics_omits_flight_keys(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)
        shot = _live_shot()

        payload = _finalize(shot)

        assert set(payload["derived"]) == {
            "smash_factor",
            "face_angle_deg",
            "dynamic_loft_deg",
            "spin_loft_deg",
        }

    def test_on_mock_shot_uses_display_flight(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        shot = _live_shot(mode="mock", spin_source="mock")

        payload = _finalize(shot)

        assert payload["derived"]["smash_factor"]["source"] == "estimated"
        assert "apex_yards" in payload["derived"]

    def test_on_logs_once_per_shot(self, monkeypatch, emitted, caplog):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        with caplog.at_level("INFO", logger="openflight.server"):
            _finalize(_live_shot())
        lines = [r.getMessage() for r in caplog.records if "Derived metrics:" in r.getMessage()]
        assert len(lines) == 1
        assert "shot_shape=" in lines[0]

    def test_flag_sets_the_runtime_toggle(self, monkeypatch):
        for name in (
            "derived_metrics_enabled",
            "derived_metrics_strict_enabled",
            "show_normalized_carry",
            "ballistics_enabled",
            "air_density",
            "calculated_spin_enabled",
            "spin_axis_model",
            "battery_provider",
            "profile_store",
            "ball_speed_correction_enabled",
            "ball_speed_correction_distance_ft",
            "ball_speed_correction_ball_above_radar_ft",
            "_VERTICAL_RADAR_GATE_BYPASS",
            "radar_auto_reconnect_enabled",
            "sim_connectors",
        ):
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "install_signal_handlers", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *_a, **_kw: None)
        base_argv = ["openflight-server", "--mock", "--no-logging"]

        monkeypatch.setattr(sys, "argv", base_argv)
        server_module.main()
        assert server_module.derived_metrics_enabled is False

        monkeypatch.setattr(sys, "argv", [*base_argv, "--derived-metrics"])
        server_module.main()
        assert server_module.derived_metrics_enabled is True
        assert server_module.derived_metrics_strict_enabled is False

        monkeypatch.setattr(
            sys, "argv", [*base_argv, "--derived-metrics", "--derived-metrics-strict"]
        )
        server_module.main()
        assert server_module.derived_metrics_strict_enabled is True


HORIZONTAL_KEYS = {"face_angle_deg", "face_to_path_deg", "curve_yards", "side_yards", "shot_shape"}
LOFT_KEYS = {"dynamic_loft_deg", "spin_loft_deg"}


class TestStrictDerive:
    def test_lenient_default_keeps_estimated_horizontal_and_loft_keys(self):
        shot = _live_shot(
            launch_angle_horizontal=0.0,
            launch_angle_horizontal_source="estimated",
            launch_angle_vertical_source="estimated",
            spin_axis_deg=None,
            club_path_deg=-1.0,
        )
        derived = derive(shot, _trajectory())
        assert HORIZONTAL_KEYS | LOFT_KEYS <= set(derived)

    def test_strict_omits_horizontal_keys_for_estimated_start_direction(self):
        shot = _live_shot(launch_angle_horizontal=0.0, launch_angle_horizontal_source="estimated")
        derived = derive(shot, _trajectory(), strict=True)
        assert not HORIZONTAL_KEYS & set(derived)
        assert LOFT_KEYS <= set(derived)
        assert "apex_yards" in derived

    def test_strict_omits_horizontal_keys_without_spin_axis(self):
        derived = derive(_live_shot(spin_axis_deg=None), _trajectory(), strict=True)
        assert not HORIZONTAL_KEYS & set(derived)

    def test_strict_omits_loft_keys_for_table_vertical_launch(self):
        shot = _live_shot(launch_angle_vertical_source="estimated", club_path_deg=-1.0)
        derived = derive(shot, _trajectory(), strict=True)
        assert not LOFT_KEYS & set(derived)
        assert HORIZONTAL_KEYS <= set(derived)

    def test_strict_keeps_everything_for_measured_angles_and_spin_axis(self):
        shot = _live_shot(club_path_deg=-1.0, club_angle_deg=2.0)
        assert derive(shot, _trajectory(), strict=True) == derive(shot, _trajectory())


class TestStrictServerPayload:
    @staticmethod
    def _radar_only_shot() -> Shot:
        return _live_shot(
            launch_angle_vertical=None,
            launch_angle_vertical_source=None,
            launch_angle_horizontal=None,
            launch_angle_horizontal_source=None,
            spin_axis_deg=None,
        )

    def test_radar_only_shot_reports_neutral_face_and_shape_when_off(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        assert server_module.derived_metrics_strict_enabled is False

        derived = _finalize(self._radar_only_shot())["derived"]

        assert derived["face_angle_deg"]["value"] == 0.0
        assert derived["shot_shape"]["value"] == "straight"
        assert LOFT_KEYS <= set(derived)

    def test_radar_only_shot_omits_fabricated_keys_when_on(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        monkeypatch.setattr(server_module, "derived_metrics_strict_enabled", True)

        derived = _finalize(self._radar_only_shot())["derived"]

        assert not (HORIZONTAL_KEYS | LOFT_KEYS) & set(derived)
        assert {"smash_factor", "apex_yards", "total_yards"} <= set(derived)
