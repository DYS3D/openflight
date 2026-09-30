"""Inclinometer roll: measurement, IWR6843 level-frame compensation, level warning."""

import math
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.inclinometer import (
    AccelerationSample,
    InclinometerService,
    OrientationSnapshot,
    SnapshotSelection,
)
from openflight.inclinometer.level import (
    LEVEL_CLEAR_FRACTION,
    LevelMonitor,
    level_frame_angles,
)
from openflight.inclinometer.service import pitch_degrees, roll_degrees
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot


def _tilted_sample(*, pitch_deg=0.0, roll_deg=0.0, timestamp=1.0) -> AccelerationSample:
    """Specific force with +Y raised ``pitch_deg`` and +X (right) lowered ``roll_deg``."""
    x_g = -math.sin(math.radians(roll_deg))
    y_g = math.sin(math.radians(pitch_deg))
    return AccelerationSample(timestamp, x_g, y_g, math.sqrt(1.0 - x_g * x_g - y_g * y_g))


class TestRollMeasurement:
    def test_right_side_down_is_positive_roll(self):
        assert roll_degrees(_tilted_sample(roll_deg=3.0)) == pytest.approx(3.0)
        assert roll_degrees(_tilted_sample(roll_deg=-2.0)) == pytest.approx(-2.0)

    def test_roll_and_pitch_are_independent(self):
        sample = _tilted_sample(pitch_deg=4.0, roll_deg=-6.0)

        assert pitch_degrees(sample) == pytest.approx(4.0)
        assert roll_degrees(sample) == pytest.approx(-6.0)

    def test_level_unit_has_zero_roll(self):
        assert roll_degrees(AccelerationSample(1.0, 0.0, 0.0, 1.0)) == 0.0

    def test_service_snapshot_reports_median_roll(self):
        service = InclinometerService(sensor=None, zero_offset_deg=1.0, window_samples=4)
        snapshot = None
        for index, roll in enumerate((2.0, 2.2, 1.8, 2.0)):
            snapshot = service.add_sample(
                _tilted_sample(pitch_deg=3.0, roll_deg=roll, timestamp=index * 0.1)
            )

        assert snapshot.roll_deg == pytest.approx(2.0, abs=1e-6)
        assert snapshot.calibrated_pitch_deg == pytest.approx(4.0)
        assert snapshot.to_dict()["roll_deg"] == pytest.approx(2.0, abs=1e-3)


class TestLevelFrameAngles:
    def test_zero_roll_is_identity(self):
        assert level_frame_angles(14.0, -3.0, 0.0) == pytest.approx((14.0, -3.0))

    def test_ninety_degree_roll_turns_radar_up_into_right(self):
        vertical, horizontal = level_frame_angles(10.0, 0.0, 90.0)

        assert vertical == pytest.approx(0.0, abs=1e-9)
        assert horizontal == pytest.approx(10.0)

    def test_ninety_degree_roll_turns_radar_right_into_down(self):
        vertical, horizontal = level_frame_angles(0.0, 10.0, 90.0)

        assert vertical == pytest.approx(-10.0)
        assert horizontal == pytest.approx(0.0, abs=1e-9)

    def test_small_angle_behaviour(self):
        roll = 2.0
        vertical, horizontal = level_frame_angles(12.0, 1.0, roll)

        roll_rad = math.radians(roll)
        assert horizontal == pytest.approx(1.0 + roll_rad * 12.0, abs=0.02)
        assert vertical == pytest.approx(12.0 - roll_rad * 1.0, abs=0.02)

    def test_opposite_roll_undoes_the_rotation(self):
        leveled = level_frame_angles(25.0, -4.0, 3.5)

        assert level_frame_angles(*leveled, -3.5) == pytest.approx((25.0, -4.0))


class TestLevelMonitor:
    def test_rejects_non_positive_threshold(self):
        with pytest.raises(ValueError):
            LevelMonitor(0.0)

    def test_first_reading_is_a_change(self):
        monitor = LevelMonitor(2.0)

        assert monitor.status is None
        assert monitor.update(0.5, -0.4) is True
        assert monitor.status == {
            "pitch_deg": 0.5,
            "roll_deg": -0.4,
            "level": True,
            "threshold_deg": 2.0,
        }

    def test_warns_above_threshold_on_either_axis(self):
        monitor = LevelMonitor(2.0)
        monitor.update(0.0, 0.0)

        assert monitor.update(0.0, -2.1) is True
        assert monitor.status["level"] is False

    def test_hysteresis_holds_warning_until_below_clear_fraction(self):
        monitor = LevelMonitor(2.0)
        monitor.update(2.5, 0.0)
        clear_below = 2.0 * LEVEL_CLEAR_FRACTION

        assert monitor.update(1.9, 0.0) is False
        assert monitor.update(clear_below + 0.01, 0.0) is False
        assert monitor.status["level"] is False
        assert monitor.update(clear_below - 0.01, 0.0) is True
        assert monitor.status["level"] is True

    def test_no_flapping_around_the_threshold(self):
        monitor = LevelMonitor(2.0)
        changes = [monitor.update(value, 0.0) for value in (1.0, 2.05, 1.95, 2.05, 1.95, 2.05)]

        assert changes == [True, True, False, False, False, False]


class FakeInclinometer:
    def __init__(self, selection):
        self.selection = selection

    def snapshot_for_impact(self, _impact_timestamp):
        return self.selection


def _selection(*, pitch_deg=0.0, roll_deg=0.0) -> SnapshotSelection:
    return SnapshotSelection(
        snapshot=OrientationSnapshot(
            timestamp=99.8,
            x_g=0.0,
            y_g=0.0,
            z_g=1.0,
            gravity_g=1.0,
            raw_pitch_deg=pitch_deg,
            calibrated_pitch_deg=pitch_deg,
            pitch_std_deg=0.1,
            sample_count=8,
            roll_deg=roll_deg,
        ),
        status="stable",
        age_s=0.2,
    )


@pytest.fixture
def socket_events(monkeypatch):
    events = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_kw: events.append((event, payload)),
    )
    return events


class TestLevelStatusEvents:
    def test_disabled_by_default(self, monkeypatch, socket_events):
        assert server_module.level_monitor is None
        monkeypatch.setattr(server_module, "inclinometer_service", FakeInclinometer(_selection()))

        server_module._poll_level_status()
        self._connect(monkeypatch)

        assert "level_status" not in [name for name, _ in socket_events]

    def test_broadcasts_only_when_level_changes(self, monkeypatch, socket_events):
        sensor = FakeInclinometer(_selection(pitch_deg=0.3, roll_deg=0.2))
        monkeypatch.setattr(server_module, "inclinometer_service", sensor)
        monkeypatch.setattr(server_module, "level_monitor", LevelMonitor(1.5))

        server_module._poll_level_status()
        server_module._poll_level_status()
        sensor.selection = _selection(pitch_deg=0.4, roll_deg=1.7)
        server_module._poll_level_status()
        sensor.selection = _selection(pitch_deg=0.4, roll_deg=1.4)
        server_module._poll_level_status()
        sensor.selection = _selection(pitch_deg=0.1, roll_deg=0.9)
        server_module._poll_level_status()

        level_events = [payload for name, payload in socket_events if name == "level_status"]
        assert level_events == [
            {"pitch_deg": 0.3, "roll_deg": 0.2, "level": True, "threshold_deg": 1.5},
            {"pitch_deg": 0.4, "roll_deg": 1.7, "level": False, "threshold_deg": 1.5},
            {"pitch_deg": 0.1, "roll_deg": 0.9, "level": True, "threshold_deg": 1.5},
        ]

    def test_moving_or_stale_readings_keep_the_last_state(self, monkeypatch, socket_events):
        sensor = FakeInclinometer(SnapshotSelection(snapshot=None, status="moving"))
        monkeypatch.setattr(server_module, "inclinometer_service", sensor)
        monkeypatch.setattr(server_module, "level_monitor", LevelMonitor(1.5))

        server_module._poll_level_status()

        assert socket_events == []
        assert server_module.level_monitor.status is None

    def test_connect_sends_the_current_status(self, monkeypatch, socket_events):
        monitor = LevelMonitor(1.5)
        monitor.update(2.0, 0.0)
        monkeypatch.setattr(server_module, "level_monitor", monitor)

        self._connect(monkeypatch)

        assert ("level_status", monitor.status) in socket_events

    def test_start_runs_the_background_poll(self, monkeypatch, socket_events):
        monkeypatch.setattr(
            server_module, "inclinometer_service", FakeInclinometer(_selection(pitch_deg=3.0))
        )
        monkeypatch.setattr(server_module, "LEVEL_STATUS_POLL_S", 0.01)
        monkeypatch.setattr(server_module, "level_monitor", None)
        try:
            server_module.start_level_status_monitor(2.0)
            deadline = time.monotonic() + 2.0
            while not socket_events and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            server_module._level_status_stop.set()

        assert socket_events[0][0] == "level_status"
        assert socket_events[0][1]["level"] is False

    @staticmethod
    def _connect(monkeypatch):
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "power_monitor", None)
        monkeypatch.setattr(server_module, "update_service", None)
        monkeypatch.setattr(server_module, "_emit_sim_snapshot", lambda: None)
        server_module.handle_connect()


def _iwr_runtime(measurement, club_path=None):
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(11.5)

    def process_shot(**_kwargs):
        return SimpleNamespace(
            capture=SimpleNamespace(valid=True, sequence=1, error=None),
            measurement=measurement,
            club_path=club_path,
        )

    return SimpleNamespace(calibration=calibration, process_shot=process_shot)


def _measurement(angle_deg=14.0, horizontal_deg=-2.0):
    return SimpleNamespace(
        accepted=True,
        angle_deg=angle_deg,
        horizontal_deg=horizontal_deg,
        horizontal_confidence=0.8,
        horizontal_status="accepted",
        single_channel=False,
        component_std_deg=1.0,
        n_snapshots=8,
        n_frames=8,
        status="accepted",
        to_dict=lambda: {},
    )


def _club_path(path_deg=3.0, attack_deg=-4.0):
    return SimpleNamespace(
        accepted=True,
        path_deg=path_deg,
        candidate_path_deg=path_deg,
        candidate_attack_angle_deg=attack_deg,
        candidate_path_status="candidate_available",
        attack_angle_status="accepted",
        status="accepted",
        confidence=0.9,
        n_frames=6,
        to_dict=lambda: {},
    )


class TestRollCompensation:
    @pytest.fixture(autouse=True)
    def _server(self, monkeypatch, socket_events):
        monkeypatch.setattr(
            server_module,
            "inclinometer_service",
            FakeInclinometer(_selection(pitch_deg=1.0, roll_deg=3.0)),
        )
        monkeypatch.setattr(server_module, "inclinometer_runtime_config", {"zero_offset_deg": 0.0})
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

    def _process(self, monkeypatch, measurement, club_path=None):
        monkeypatch.setattr(server_module, "iwr6843_runtime", _iwr_runtime(measurement, club_path))
        shot = Shot(ball_speed_mph=120.0, timestamp=datetime.now(), impact_timestamp=100.0)
        server_module._snapshot_inclinometer_for_shot(shot)
        server_module._process_iwr6843_angle(shot)
        return shot

    def test_off_by_default_leaves_iwr_angles_in_the_radar_frame(self, monkeypatch):
        assert server_module.inclinometer_roll_compensation_enabled is False

        shot = self._process(monkeypatch, _measurement(), _club_path())

        assert shot.launch_angle_vertical == 14.0
        assert shot.launch_angle_horizontal == -2.0
        assert shot.iwr6843_horizontal_deg == -2.0
        assert shot.experimental_club_path_deg == 3.0
        assert shot.experimental_attack_angle_deg == -4.0
        assert "roll_compensation" not in shot.inclinometer
        assert shot.inclinometer["roll_deg"] == 3.0

    def test_on_rotates_ball_and_club_angles_by_roll(self, monkeypatch):
        monkeypatch.setattr(server_module, "inclinometer_roll_compensation_enabled", True)

        shot = self._process(monkeypatch, _measurement(), _club_path())

        vertical, horizontal = level_frame_angles(14.0, -2.0, 3.0)
        attack, path = level_frame_angles(-4.0, 3.0, 3.0)
        assert shot.launch_angle_vertical == pytest.approx(vertical)
        assert shot.launch_angle_horizontal == pytest.approx(horizontal)
        assert shot.iwr6843_horizontal_deg == pytest.approx(horizontal)
        assert shot.experimental_club_path_deg == round(path, 1)
        assert shot.experimental_attack_angle_deg == round(attack, 1)
        record = shot.inclinometer["roll_compensation"]
        assert record["ball"]["radar_vertical_deg"] == 14.0
        assert record["ball"]["radar_horizontal_deg"] == -2.0
        assert record["club"]["roll_deg"] == 3.0

    def test_on_without_horizontal_keeps_vertical(self, monkeypatch):
        monkeypatch.setattr(server_module, "inclinometer_roll_compensation_enabled", True)

        shot = self._process(monkeypatch, _measurement(horizontal_deg=None))

        assert shot.launch_angle_vertical == 14.0
        assert shot.launch_angle_horizontal is None
        assert "roll_compensation" not in shot.inclinometer

    def test_on_without_a_stable_snapshot_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(server_module, "inclinometer_roll_compensation_enabled", True)
        monkeypatch.setattr(
            server_module,
            "inclinometer_service",
            FakeInclinometer(SnapshotSelection(snapshot=None, status="stale")),
        )

        shot = self._process(monkeypatch, _measurement())

        assert shot.launch_angle_vertical == 14.0
        assert shot.launch_angle_horizontal == -2.0


class TestFlags:
    @pytest.mark.parametrize(
        "flags",
        [
            ["--inclinometer-roll-compensation"],
            ["--level-warning-deg", "2"],
            ["--level-warning-deg", "-1"],
        ],
    )
    def test_invalid_combinations_fail_at_the_cli(self, monkeypatch, flags):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", *flags])

        with pytest.raises(SystemExit, match="2"):
            server_module.main()

    def test_defaults_are_off(self, monkeypatch):
        for name in (
            "inclinometer_roll_compensation_enabled",
            "ballistics_enabled",
            "air_density",
            "calculated_spin_enabled",
            "spin_axis_model",
            "show_normalized_carry",
            "battery_provider",
            "profile_store",
            "ball_speed_correction_enabled",
            "ball_speed_correction_distance_ft",
            "ball_speed_correction_ball_above_radar_ft",
            "_VERTICAL_RADAR_GATE_BYPASS",
            "radar_auto_reconnect_enabled",
            "sim_connectors",
            "level_monitor",
        ):
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging"])
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "install_signal_handlers", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *_a, **_kw: None)

        server_module.main()

        assert server_module.inclinometer_roll_compensation_enabled is False
        assert server_module.level_monitor is None
