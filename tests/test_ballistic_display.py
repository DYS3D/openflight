"""Ballistic display data in UI shot payloads: flight path and normalized carry."""

import json
import sys
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ballistics import air_density_kg_m3, resolve_launch, simulate
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger
from openflight.swing_speed import SwingSpeedEvent


def _wait_for_shot_finalization_idle(timeout_s: float = 2.0) -> None:
    with server_module._shot_finalization_condition:
        idle = server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=timeout_s,
        )
    assert idle, "shot finalization coordinator did not become idle"


@pytest.fixture
def emitted(monkeypatch):
    """Run finalization with no optional hardware and capture socket events."""
    events = []
    server_module._reset_shot_sequence()
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "air_density", server_module.AIR_DENSITY_STD)
    monkeypatch.setattr(server_module, "show_normalized_carry", False)
    monkeypatch.setattr(server_module, "debug_mode", False)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_kw: events.append((event, payload)),
    )
    yield events
    _wait_for_shot_finalization_idle()


def _live_shot(**overrides) -> Shot:
    values = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 102.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
        "launch_angle_vertical": 12.0,
        "launch_angle_vertical_confidence": 0.9,
        "launch_angle_vertical_source": "radar",
        "launch_angle_horizontal": 2.0,
        "launch_angle_horizontal_confidence": 0.9,
        "launch_angle_horizontal_source": "radar",
        "spin_axis_deg": 5.0,
        "spin_rpm": 2600.0,
        "spin_confidence": 0.9,
        "spin_source": "measured",
    }
    values.update(overrides)
    return Shot(**values)


def _finalize(shot: Shot) -> dict:
    server_module.on_shot_detected(shot)
    _wait_for_shot_finalization_idle()
    return server_module.shot_to_dict(shot)


class TestFlightPayloadShape:
    def test_downsamples_to_forty_points_ending_at_landing(self):
        trajectory = simulate(resolve_launch(_live_shot()))
        assert len(trajectory.points) > server_module.FLIGHT_MAX_POINTS

        flight = server_module._flight_payload(trajectory)

        assert len(flight["points"]) == server_module.FLIGHT_MAX_POINTS
        assert flight["points"][0] == [0.0, 0.0, 0.0]
        landing = trajectory.points[-1]
        assert flight["points"][-1] == [
            round(landing.x, 2),
            round(landing.y, 2),
            round(landing.z, 2),
        ]
        downrange = [point[0] for point in flight["points"]]
        assert downrange == sorted(downrange)

    def test_summary_fields_are_yards_degrees_and_seconds(self):
        trajectory = simulate(resolve_launch(_live_shot()))

        flight = server_module._flight_payload(trajectory)

        assert flight["carry_yards"] == round(trajectory.carry_yards, 1)
        assert flight["lateral_yards"] == round(trajectory.lateral_yards, 1)
        assert flight["apex_yards"] == round(trajectory.apex_yards, 1)
        assert flight["landing_angle_deg"] == round(trajectory.landing_angle_deg, 1)
        assert flight["flight_time_s"] == round(trajectory.flight_time_s, 2)
        assert max(point[2] for point in flight["points"]) <= flight["apex_yards"] + 0.05

    def test_short_flights_keep_every_point(self):
        trajectory = simulate(resolve_launch(_live_shot(ball_speed_mph=40.0)))
        trajectory.points = trajectory.points[:12]

        assert len(server_module._flight_payload(trajectory)["points"]) == 12

    def test_scaling_moves_landing_to_requested_carry_only_downrange_and_lateral(self):
        trajectory = simulate(resolve_launch(_live_shot()))
        target = trajectory.carry_yards * 0.9

        flight = server_module._flight_payload(trajectory, carry_yards=target)

        assert flight["points"][-1][0] == pytest.approx(target, abs=0.01)
        assert flight["carry_yards"] == round(target, 1)
        assert flight["lateral_yards"] == round(trajectory.lateral_yards * 0.9, 1)
        assert flight["apex_yards"] == round(trajectory.apex_yards, 1)


class TestLiveShotFlight:
    def test_live_shot_payload_carries_the_simulated_flight(self, emitted):
        shot = _live_shot()

        payload = _finalize(shot)

        flight = payload["flight"]
        assert 2 <= len(flight["points"]) <= server_module.FLIGHT_MAX_POINTS
        assert flight["points"][-1][0] == pytest.approx(shot.carry_spin_adjusted, abs=0.05)
        assert flight["carry_yards"] == pytest.approx(shot.carry_spin_adjusted, abs=0.05)
        assert flight["lateral_yards"] > 0  # fade axis and right start drift right
        final_event, final_payload = emitted[-1]
        assert final_event == "shot"
        assert final_payload["shot"]["flight"] == flight

    def test_flight_is_listed_in_session_state(self, monkeypatch, emitted):
        shot = _live_shot()
        _finalize(shot)

        class Monitor:
            @staticmethod
            def get_shots():
                return [shot]

        monkeypatch.setattr(server_module, "monitor", Monitor())

        assert server_module._session_shots()[0]["flight"] == shot.flight

    def test_table_fallback_has_no_flight(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "ballistics_enabled", False)

        payload = _finalize(_live_shot())

        assert "flight" not in payload

    def test_session_log_entry_has_no_flight(self, monkeypatch, tmp_path, emitted):
        session_log = SessionLogger(log_dir=tmp_path, enabled=True)
        session_log.start_session(mode="rolling-buffer", trigger_type="sound")
        monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
        shot = _live_shot()

        _finalize(shot)
        session_log.flush()

        entries = [json.loads(line) for line in session_log.session_path.read_text().splitlines()]
        logged = [entry for entry in entries if entry["type"] == "shot_detected"]
        assert shot.flight is not None
        assert len(logged) == 1
        assert "flight" not in logged[0]
        assert "flight" not in shot.to_dict()
        session_log.end_session()


class TestMockShotFlight:
    def test_mock_flight_lands_at_displayed_carry_without_changing_it(self, emitted):
        mock = server_module.MockLaunchMonitor()
        mock.set_club(ClubType.IRON_7)
        mock.start(shot_callback=server_module.on_shot_detected)

        shot = mock.simulate_shot(ball_speed=120.0)
        _wait_for_shot_finalization_idle()
        payload = server_module.shot_to_dict(shot)

        assert shot.carry_spin_adjusted is None
        assert payload["carry_spin_adjusted"] is None
        displayed = shot.estimated_carry_yards
        assert payload["estimated_carry_yards"] == round(displayed)
        flight = payload["flight"]
        assert len(flight["points"]) <= server_module.FLIGHT_MAX_POINTS
        assert flight["points"][-1][0] == pytest.approx(displayed, abs=0.01)
        assert flight["carry_yards"] == pytest.approx(displayed, abs=0.05)

    def test_attaching_mock_flight_changes_no_shot_field(self):
        shot = _live_shot(mode="mock", spin_source="mock")
        before = shot.to_dict()

        server_module._attach_mock_flight(shot)

        assert shot.flight is not None
        assert shot.to_dict() == before


class TestNormalizedCarry:
    def test_standard_conditions_are_trackman_normalization(self):
        assert server_module.NORMALIZED_AIR_DENSITY == pytest.approx(
            air_density_kg_m3(altitude_m=0.0, temperature_c=25.0, relative_humidity=0.0)
        )
        assert server_module.NORMALIZED_AIR_DENSITY == pytest.approx(1.184, abs=0.001)

    def test_off_by_default_key_absent(self, emitted):
        assert server_module.show_normalized_carry is False
        shot = _live_shot()

        payload = _finalize(shot)

        assert shot.carry_normalized_yards is None
        assert "carry_normalized_yards" not in payload
        assert "carry_normalized_yards" not in shot.to_dict()

    def test_on_equals_simulation_at_standard_density(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        shot = _live_shot()

        payload = _finalize(shot)

        expected = simulate(
            resolve_launch(shot), air_density=server_module.NORMALIZED_AIR_DENSITY
        ).carry_yards
        assert shot.carry_normalized_yards == pytest.approx(expected)
        assert payload["carry_normalized_yards"] == round(expected)
        assert shot.to_dict()["carry_normalized_yards"] == pytest.approx(expected)

    def test_on_follows_the_shot_as_an_update_so_it_never_delays_it(
        self, monkeypatch, emitted
    ):
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        _finalize(_live_shot())

        events = [event for event, _payload in emitted if event in ("shot", "shot_update")]
        assert events == ["shot", "shot_update"]
        first = next(p for e, p in emitted if e == "shot")["shot"]
        update = next(p for e, p in emitted if e == "shot_update")["shot"]
        assert "carry_normalized_yards" not in first
        assert update["carry_normalized_yards"] > 0
        assert update["timestamp"] == first["timestamp"]

    def test_off_sends_no_update(self, emitted):
        _finalize(_live_shot())
        assert [e for e, _p in emitted if e == "shot_update"] == []

    def test_on_differs_from_actual_carry_at_altitude(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        monkeypatch.setattr(server_module, "air_density", air_density_kg_m3(altitude_m=1609.0))
        shot = _live_shot()

        payload = _finalize(shot)

        assert shot.carry_spin_adjusted - shot.carry_normalized_yards > 5.0
        assert payload["carry_normalized_yards"] < payload["carry_spin_adjusted"]

    def test_on_is_logged_in_the_session_shot_entry(self, monkeypatch, tmp_path, emitted):
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        session_log = SessionLogger(log_dir=tmp_path, enabled=True)
        session_log.start_session(mode="rolling-buffer", trigger_type="sound")
        monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
        shot = _live_shot()

        _finalize(shot)
        session_log.flush()

        entries = [json.loads(line) for line in session_log.session_path.read_text().splitlines()]
        logged = [entry for entry in entries if entry["type"] == "shot_detected"]
        assert logged[0]["carry_normalized_yards"] == pytest.approx(shot.carry_normalized_yards)
        session_log.end_session()

    @pytest.mark.parametrize("ballistics", [False, True])
    def test_absent_without_the_physics_simulation(self, monkeypatch, emitted, ballistics):
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        monkeypatch.setattr(server_module, "ballistics_enabled", ballistics)
        shot = _live_shot(mode="mock", spin_source="mock") if ballistics else _live_shot()

        payload = _finalize(shot)

        assert "carry_normalized_yards" not in payload

    def test_flag_sets_the_runtime_toggle(self, monkeypatch):
        for name in (
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
        assert server_module.show_normalized_carry is False

        monkeypatch.setattr(sys, "argv", [*base_argv, "--show-normalized-carry"])
        server_module.main()
        assert server_module.show_normalized_carry is True


class TestSwingSpeedPayload:
    def test_swing_speed_payload_has_no_ballistic_display_data(self):
        event = SwingSpeedEvent(
            peak_speed_mph=95.0,
            timestamp=datetime.now(),
            duration_ms=1000.0,
            reading_count=5,
            trigger_speed_mph=70.0,
            peak_magnitude=200.0,
        )

        payload = server_module.swing_speed_to_shot_dict(event)

        assert "flight" not in payload
        assert "carry_normalized_yards" not in payload
