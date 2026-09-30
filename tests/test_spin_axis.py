"""D-plane spin-axis model and its --spin-axis-model server wiring."""

import math
import sys
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ballistics import resolve_launch
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState
from openflight.spin_axis import (
    DPLANE_ANCHORS,
    MAX_SPIN_AXIS_DEG,
    SPIN_LOFT_RANGE_DEG,
    dplane_club_parameters,
    dplane_spin_axis,
    spin_axis_tilt_deg,
)


class TestSpinAxisTilt:
    def test_reproduces_tutelman_worked_example(self):
        # 23 deg spin loft, 7 deg face to path -> d = 0.289 -> ~16.1 deg tilt.
        assert spin_axis_tilt_deg(7.0, 23.0) == pytest.approx(16.1, abs=0.5)
        assert math.tan(math.radians(7.0)) / math.tan(math.radians(23.0)) == pytest.approx(
            0.289, abs=0.001
        )

    def test_open_face_is_fade_positive_and_closed_face_is_draw_negative(self):
        assert spin_axis_tilt_deg(4.0, 20.0) > 0
        assert spin_axis_tilt_deg(-4.0, 20.0) < 0
        assert spin_axis_tilt_deg(-4.0, 20.0) == pytest.approx(-spin_axis_tilt_deg(4.0, 20.0))

    def test_square_face_has_no_tilt(self):
        assert spin_axis_tilt_deg(0.0, 20.0) == 0.0

    def test_tilt_is_clamped(self):
        assert spin_axis_tilt_deg(40.0, 10.0) == MAX_SPIN_AXIS_DEG
        assert spin_axis_tilt_deg(-40.0, 10.0) == -MAX_SPIN_AXIS_DEG
        assert spin_axis_tilt_deg(120.0, 10.0) == MAX_SPIN_AXIS_DEG

    def test_spin_loft_is_clamped(self):
        low, high = SPIN_LOFT_RANGE_DEG
        assert spin_axis_tilt_deg(2.0, 0.5) == pytest.approx(spin_axis_tilt_deg(2.0, low))
        assert spin_axis_tilt_deg(2.0, 80.0) == pytest.approx(spin_axis_tilt_deg(2.0, high))


class TestClubParameters:
    def test_published_anchor_values(self):
        driver = dplane_club_parameters(ClubType.DRIVER)
        seven = dplane_club_parameters(ClubType.IRON_7)
        assert (driver.face_weight, driver.launch_ratio) == (0.76, 0.87)
        assert (seven.face_weight, seven.launch_ratio) == (0.69, 0.75)
        assert driver.attack_angle_deg == pytest.approx(-1.3)
        assert seven.attack_angle_deg == pytest.approx(-4.3)

    def test_interpolates_between_anchors_by_loft(self):
        five_iron = dplane_club_parameters(ClubType.IRON_5)  # 27 deg nominal loft
        assert 0.69 < five_iron.face_weight < 0.73
        assert 0.75 < five_iron.launch_ratio < 0.83
        assert -4.3 < five_iron.attack_angle_deg < -3.3

    def test_holds_end_anchor_outside_the_table(self):
        lob = dplane_club_parameters(ClubType.LW)
        assert lob.face_weight == DPLANE_ANCHORS[-1].face_weight
        assert lob.attack_angle_deg == DPLANE_ANCHORS[-1].attack_angle_deg


class TestDPlaneEstimate:
    def test_face_to_path_uses_club_start_direction_weight(self):
        estimate = dplane_spin_axis(
            launch_horizontal_deg=3.0,
            club_path_deg=-1.0,
            launch_vertical_deg=11.0,
            club=ClubType.DRIVER,
        )
        assert estimate.face_to_path_deg == pytest.approx(4.0 / 0.76)
        assert estimate.dynamic_loft_deg == pytest.approx(11.0 / 0.87)
        assert estimate.attack_angle_source == "club_default"
        assert estimate.spin_loft_deg == pytest.approx(11.0 / 0.87 + 1.3)
        # D-plane tilt is several times the legacy HLA - path difference.
        assert estimate.spin_axis_deg > 3 * 4.0

    def test_measured_attack_angle_replaces_the_club_default(self):
        estimate = dplane_spin_axis(
            launch_horizontal_deg=2.0,
            club_path_deg=0.0,
            launch_vertical_deg=20.0,
            club=ClubType.IRON_7,
            attack_angle_deg=-6.0,
        )
        assert estimate.attack_angle_source == "measured"
        assert estimate.spin_loft_deg == pytest.approx(20.0 / 0.75 + 6.0)

    def test_draw_is_negative(self):
        estimate = dplane_spin_axis(
            launch_horizontal_deg=-1.0,
            club_path_deg=3.0,
            launch_vertical_deg=20.0,
            club=ClubType.IRON_7,
        )
        assert estimate.spin_axis_deg < 0


def _spin_axis_shot(**overrides) -> Shot:
    values = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 100.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
        "launch_angle_horizontal": 3.2,
        "launch_angle_horizontal_confidence": 0.8,
        "club_path_deg": -1.5,
        "launch_angle_vertical": 11.0,
    }
    values.update(overrides)
    return Shot(**values)


class TestServerSpinAxisModel:
    def test_default_model_is_legacy(self):
        assert server_module.spin_axis_model == "legacy"

    def test_legacy_output_unchanged(self, monkeypatch):
        monkeypatch.setattr(server_module, "spin_axis_model", "legacy")
        shot = _spin_axis_shot()

        server_module._derive_spin_axis(shot)

        assert shot.spin_axis_deg == pytest.approx(round(3.2 - (-1.5), 1))

    def test_dplane_uses_face_to_path_and_spin_loft(self, monkeypatch):
        monkeypatch.setattr(server_module, "spin_axis_model", "dplane")
        shot = _spin_axis_shot()

        server_module._derive_spin_axis(shot)

        expected = dplane_spin_axis(
            launch_horizontal_deg=3.2,
            club_path_deg=-1.5,
            launch_vertical_deg=11.0,
            club=ClubType.DRIVER,
        ).spin_axis_deg
        assert shot.spin_axis_deg == pytest.approx(round(expected, 1))
        assert shot.spin_axis_deg > 3.2 - (-1.5)

    def test_dplane_uses_measured_attack_angle(self, monkeypatch):
        monkeypatch.setattr(server_module, "spin_axis_model", "dplane")
        shot = _spin_axis_shot(club_angle_deg=4.0)

        server_module._derive_spin_axis(shot)

        expected = dplane_spin_axis(
            launch_horizontal_deg=3.2,
            club_path_deg=-1.5,
            launch_vertical_deg=11.0,
            club=ClubType.DRIVER,
            attack_angle_deg=4.0,
        ).spin_axis_deg
        assert shot.spin_axis_deg == pytest.approx(round(expected, 1))

    def test_dplane_without_vertical_launch_keeps_legacy_value(self, monkeypatch):
        monkeypatch.setattr(server_module, "spin_axis_model", "dplane")
        shot = _spin_axis_shot(launch_angle_vertical=None)

        server_module._derive_spin_axis(shot)

        assert shot.spin_axis_deg == pytest.approx(round(3.2 - (-1.5), 1))

    @pytest.mark.parametrize("model", ["legacy", "dplane"])
    @pytest.mark.parametrize(
        "overrides",
        [
            {"club_path_deg": None},
            {"launch_angle_horizontal": None},
            {"launch_angle_horizontal_confidence": 0.59},
        ],
    )
    def test_gates_are_shared_by_both_models(self, monkeypatch, model, overrides):
        monkeypatch.setattr(server_module, "spin_axis_model", model)
        shot = _spin_axis_shot(**overrides)

        server_module._derive_spin_axis(shot)

        assert shot.spin_axis_deg is None

    def test_dplane_axis_reaches_ballistics_and_simulators(self, monkeypatch):
        monkeypatch.setattr(server_module, "spin_axis_model", "dplane")
        shot = _spin_axis_shot()

        server_module._derive_spin_axis(shot)

        assert resolve_launch(shot).spin_axis_deg == shot.spin_axis_deg
        resolved = resolve_shot(shot, PlayerState(shot_counter=0))
        assert resolved.spin_axis_deg == shot.spin_axis_deg
        assert resolved.provenance["spin_axis"] == "measured"


class TestSpinAxisModelFlag:
    def _parse(self, monkeypatch, *flags):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", *flags])
        for name in (
            "spin_axis_model",
            "ballistics_enabled",
            "air_density",
            "calculated_spin_enabled",
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
        server_module.main()
        return server_module.spin_axis_model

    def test_default_is_legacy(self, monkeypatch):
        assert self._parse(monkeypatch) == "legacy"

    def test_dplane_is_opt_in(self, monkeypatch):
        assert self._parse(monkeypatch, "--spin-axis-model", "dplane") == "dplane"

    def test_rejects_unknown_model(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._parse(monkeypatch, "--spin-axis-model", "trackman")
