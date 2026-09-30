"""Live heartbeat ready flags (--gspro-ready-signals) and the legacy static frame."""

import json
import sys

import pytest

from openflight import server as server_module
from openflight.gspro.codec import GSProCodec
from openflight.gspro.messages import build_heartbeat
from openflight.sim.codec import build_connectors
from openflight.sim.config import ConnectorConfig

from .test_gspro_codec import _LEGACY_HEARTBEAT


def _flags(raw: bytes) -> tuple[bool, bool]:
    options = json.loads(raw.decode("utf-8"))["ShotDataOptions"]
    return options["LaunchMonitorIsReady"], options["LaunchMonitorBallDetected"]


class TestHeartbeatFrame:
    def test_default_heartbeat_is_byte_identical_to_legacy(self):
        assert build_heartbeat("OpenFlight", "Yards", shot_number=0) == _LEGACY_HEARTBEAT
        assert GSProCodec().heartbeat_bytes() == _LEGACY_HEARTBEAT
        assert GSProCodec(ready_state=None).heartbeat_bytes() == _LEGACY_HEARTBEAT

    @pytest.mark.parametrize("state", [(True, True), (False, False), (True, False), (False, True)])
    def test_ready_state_drives_only_the_two_flags(self, state):
        codec = GSProCodec(ready_state=lambda: state)
        raw = codec.heartbeat_bytes()
        assert _flags(raw) == state
        beat = json.loads(raw.decode("utf-8"))
        legacy = json.loads(_LEGACY_HEARTBEAT.decode("utf-8"))
        beat["ShotDataOptions"].pop("LaunchMonitorIsReady")
        beat["ShotDataOptions"].pop("LaunchMonitorBallDetected")
        legacy["ShotDataOptions"].pop("LaunchMonitorIsReady")
        legacy["ShotDataOptions"].pop("LaunchMonitorBallDetected")
        assert beat == legacy

    def test_ready_state_is_sampled_on_every_beat(self):
        states = iter([(True, True), (False, False)])
        codec = GSProCodec(ready_state=lambda: next(states))
        assert _flags(codec.heartbeat_bytes()) == (True, True)
        assert _flags(codec.heartbeat_bytes()) == (False, False)

    def test_shot_payload_flags_are_unchanged_by_ready_state(self):
        from .test_gspro_codec import _LEGACY_FULL, _resolved

        codec = GSProCodec(ready_state=lambda: (False, False))
        assert codec.build_shot(_resolved()) == _LEGACY_FULL

    def test_build_connectors_passes_ready_state_to_the_codec(self):
        cfg = ConnectorConfig(type="gspro", host="127.0.0.1", port=921, enabled=True)
        state = lambda: (False, True)  # noqa: E731
        [connector] = build_connectors([cfg], ready_state=state)
        assert connector.codec.ready_state is state
        [connector] = build_connectors([cfg])
        assert connector.codec.ready_state is None
        assert connector.codec.heartbeat_bytes() == _LEGACY_HEARTBEAT


class TestServerReadyState:
    @pytest.fixture(autouse=True)
    def idle_server(self, monkeypatch):
        server_module._reset_shot_sequence()
        monkeypatch.setattr(server_module, "monitor", object())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "_shot_processing_state", None)
        monkeypatch.setattr(
            server_module, "_get_trigger_status", lambda: {"radar_connected": True}
        )

    def test_connected_and_idle_is_ready_with_ball_mirroring_ready(self):
        assert server_module._launch_monitor_ready_state() == (True, True)

    def test_no_monitor_is_not_ready(self, monkeypatch):
        monkeypatch.setattr(server_module, "monitor", None)
        assert server_module._launch_monitor_ready_state() == (False, False)

    def test_radar_disconnected_is_not_ready(self, monkeypatch):
        monkeypatch.setattr(
            server_module, "_get_trigger_status", lambda: {"radar_connected": False}
        )
        assert server_module._launch_monitor_ready_state() == (False, False)

    def test_mock_monitor_is_ready_without_a_radar(self, monkeypatch):
        monkeypatch.setattr(server_module, "mock_mode", True)
        monkeypatch.setattr(
            server_module, "_get_trigger_status", lambda: {"radar_connected": False}
        )
        assert server_module._launch_monitor_ready_state() == (True, True)

    @pytest.mark.parametrize("state, ready", [("capturing", False), ("calculating", False), ("failed", True)])
    def test_processing_state_from_the_monitor(self, monkeypatch, state, ready):
        emitted = []
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda event, payload=None, **_kw: emitted.append(event)
        )
        server_module.on_shot_processing(state)
        assert server_module._launch_monitor_ready_state()[0] is ready
        assert emitted == ["shot_processing"]

    def test_pending_finalization_is_not_ready(self):
        with server_module._shot_finalization_condition:
            server_module._shot_finalization_order.append(1)
        try:
            assert server_module._launch_monitor_ready_state() == (False, False)
        finally:
            server_module._reset_shot_sequence()
        assert server_module._launch_monitor_ready_state() == (True, True)

    def test_camera_ball_in_zone_drives_ball_detected(self, monkeypatch):
        class Camera:
            ball_in_zone = False

        monkeypatch.setattr(server_module, "camera_capture_runtime", Camera())
        assert server_module._launch_monitor_ready_state() == (True, False)
        Camera.ball_in_zone = True
        assert server_module._launch_monitor_ready_state() == (True, True)

    def test_camera_without_ball_state_mirrors_ready(self, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", object())
        assert server_module._launch_monitor_ready_state() == (True, True)


class TestFlag:
    def test_flag_selects_the_ready_state_callable(self, monkeypatch):
        calls = []
        for name in (
            "show_normalized_carry",
            "derived_metrics_enabled",
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
        monkeypatch.setattr(server_module, "load_sim_config", lambda: [])

        def fake_build(cfgs, **kw):
            calls.append(kw)
            return []

        monkeypatch.setattr(server_module, "build_connectors", fake_build)
        base_argv = ["openflight-server", "--mock", "--no-logging", "--sim"]

        monkeypatch.setattr(sys, "argv", base_argv)
        server_module.main()
        assert calls[-1]["ready_state"] is None

        monkeypatch.setattr(sys, "argv", [*base_argv, "--gspro-ready-signals"])
        server_module.main()
        assert calls[-1]["ready_state"] is server_module._launch_monitor_ready_state
