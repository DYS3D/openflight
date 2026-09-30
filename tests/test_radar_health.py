"""OPS243 noise-floor tracking and interference flagging (--interference-check)."""

import sys
import threading
import time

import numpy as np
import pytest

from openflight import server as server_module
from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.radar_health import RadarHealthMonitor, noise_floor_db
from openflight.rolling_buffer.types import IQCapture

SAMPLE_RATE = 30000
NUM_SAMPLES = 4096


def _capture(noise_std: float, seed: int = 0, tone_mph: float | None = None) -> IQCapture:
    """Synthetic I/Q: gaussian noise around mid-scale, optionally with a ball tone."""
    rng = np.random.default_rng(seed)
    i_signal = rng.normal(0, noise_std, NUM_SAMPLES)
    q_signal = rng.normal(0, noise_std, NUM_SAMPLES)
    if tone_mph is not None:
        doppler_hz = 2 * (tone_mph / 2.23694) / 0.01243
        phase = 2 * np.pi * doppler_hz * np.arange(NUM_SAMPLES) / SAMPLE_RATE
        i_signal += 300 * np.cos(phase)
        q_signal += 300 * np.sin(phase)
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.1,
        i_samples=(2048 + i_signal).astype(int).tolist(),
        q_samples=(2048 + q_signal).astype(int).tolist(),
    )


def _quiet(seed: int = 0) -> IQCapture:
    return _capture(4.0, seed=seed)


def _noisy(seed: int = 0) -> IQCapture:
    return _capture(40.0, seed=seed)


@pytest.fixture
def processor() -> RollingBufferProcessor:
    return RollingBufferProcessor()


class TestNoiseFloor:
    def test_ten_times_the_noise_is_about_twenty_db_higher(self, processor):
        quiet = noise_floor_db(processor, _quiet())
        noisy = noise_floor_db(processor, _noisy())
        assert noisy - quiet == pytest.approx(20.0, abs=1.5)

    def test_a_ball_tone_barely_moves_the_median(self, processor):
        quiet = noise_floor_db(processor, _quiet())
        with_tone = noise_floor_db(processor, _capture(4.0, tone_mph=120.0))
        assert abs(with_tone - quiet) < 1.0

    def test_short_capture_gives_no_sample(self, processor):
        capture = IQCapture(sample_time=0.0, trigger_time=0.0, i_samples=[1] * 10, q_samples=[1] * 10)
        assert noise_floor_db(processor, capture) is None


class TestRadarHealthMonitor:
    def _monitor(self, processor, events):
        return RadarHealthMonitor(
            processor, on_change=events.append, min_interval_s=0.0, baseline_alpha=0.2
        )

    def test_quiet_noisy_quiet_flags_after_three_and_clears(self, processor, caplog):
        events = []
        health = self._monitor(processor, events)
        with caplog.at_level("INFO", logger="openflight.rolling_buffer.radar_health"):
            for seed in range(5):
                assert health.process(_quiet(seed)) is False
            assert health.process(_noisy(10)) is False
            assert health.process(_noisy(11)) is False
            assert health.process(_noisy(12)) is True
            assert health.process(_noisy(13)) is True
            assert health.process(_quiet(20)) is False

        assert [event["interference"] for event in events] == [True, False]
        assert events[0]["noise_floor_db"] - events[0]["baseline_db"] > 6.0
        assert set(events[0]) == {"interference", "noise_floor_db", "baseline_db", "updated_at"}
        assert events[0]["updated_at"].endswith("+00:00")
        levels = [r.levelname for r in caplog.records if "Interference" in r.getMessage()]
        assert levels == ["WARNING", "INFO"]

    def test_baseline_does_not_chase_interference(self, processor):
        health = self._monitor(processor, [])
        for seed in range(3):
            health.process(_quiet(seed))
        baseline = health.baseline_db
        for seed in range(10, 16):
            health.process(_noisy(seed))
        assert health.interference is True
        assert health.baseline_db == pytest.approx(baseline)

    def test_two_elevated_samples_between_quiet_ones_do_not_flag(self, processor):
        events = []
        health = self._monitor(processor, events)
        health.process(_quiet(0))
        health.process(_noisy(1))
        health.process(_noisy(2))
        health.process(_quiet(3))
        health.process(_noisy(4))
        health.process(_noisy(5))
        assert health.interference is False
        assert events == []

    def test_snapshot_starts_empty_and_reports_latest_sample(self, processor):
        health = self._monitor(processor, [])
        assert health.snapshot() == {
            "interference": False,
            "noise_floor_db": None,
            "baseline_db": None,
            "updated_at": None,
        }
        health.process(_quiet())
        snapshot = health.snapshot()
        assert snapshot["interference"] is False
        assert snapshot["noise_floor_db"] == snapshot["baseline_db"]
        assert snapshot["updated_at"]

    def test_observe_is_rate_limited_and_worker_processes_off_thread(self, processor):
        health = RadarHealthMonitor(processor, min_interval_s=60.0)
        seen = []
        original = health.process

        def record(capture):
            seen.append(threading.current_thread().name)
            return original(capture)

        health.process = record
        health.start()
        try:
            health.observe(_quiet(0))
            health.observe(_quiet(1))
            deadline = time.monotonic() + 2.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            health.stop()
        assert seen == ["radar-health"]
        assert health.noise_floor_db is not None


class TestMonitorWiring:
    @pytest.fixture
    def radar(self, monkeypatch):
        class FakeRadar:
            def __init__(self, *_a, **_kw):
                self.port = "/dev/fake"

        monkeypatch.setattr("openflight.rolling_buffer.monitor.OPS243Radar", FakeRadar)
        return FakeRadar

    def test_off_by_default_installs_nothing(self, radar):
        monitor = RollingBufferMonitor(port="/dev/fake")
        assert monitor.radar_health is None
        assert monitor.processor.capture_observer is None

    def test_on_routes_captures_and_transitions(self, radar):
        monitor = RollingBufferMonitor(port="/dev/fake", interference_check=True)
        assert monitor.processor.capture_observer == monitor.radar_health.observe
        events = []
        monitor._radar_health_callback = events.append
        monitor.radar_health.min_interval_s = 0.0
        monitor.radar_health.process(_quiet(0))
        for seed in range(1, 4):
            monitor.radar_health.process(_noisy(seed))
        assert [event["interference"] for event in events] == [True]

    def test_parse_capture_notifies_the_observer(self, processor):
        seen = []
        processor.capture_observer = seen.append
        response = (
            '{"sample_time": "1.0"}\n{"trigger_time": "1.1"}\n'
            f'{{"I": {[2048] * 256}}}\n{{"Q": {[2048] * 256}}}\n'
        )
        capture = processor.parse_capture(response)
        assert seen == [capture]


class TestServerWiring:
    def test_connect_sends_no_radar_health_without_the_flag(self, monkeypatch):
        replies = []
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "power_monitor", None)
        monkeypatch.setattr(server_module, "level_monitor", None)
        monkeypatch.setattr(server_module, "update_service", None)
        monkeypatch.setattr(server_module, "_emit_sim_snapshot", lambda: None)
        monkeypatch.setattr(server_module, "_reply", lambda event, payload=None: replies.append(event))
        server_module.handle_connect()
        assert "radar_health" not in replies

    def test_connect_sends_the_snapshot_with_the_flag(self, monkeypatch, processor):
        replies = []

        class FakeMonitor:
            radar_health = RadarHealthMonitor(processor)

            def get_session_stats(self):
                return {}

            def get_shots(self):
                return []

        monkeypatch.setattr(server_module, "monitor", FakeMonitor())
        monkeypatch.setattr(server_module, "power_monitor", None)
        monkeypatch.setattr(server_module, "level_monitor", None)
        monkeypatch.setattr(server_module, "update_service", None)
        monkeypatch.setattr(server_module, "_emit_sim_snapshot", lambda: None)
        monkeypatch.setattr(server_module, "_get_trigger_status", lambda: {})
        monkeypatch.setattr(
            server_module, "_reply", lambda event, payload=None: replies.append((event, payload))
        )
        server_module.handle_connect()
        assert ("radar_health", FakeMonitor.radar_health.snapshot()) in replies

    def test_on_radar_health_broadcasts(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload=None, **_kw: events.append((event, payload)),
        )
        server_module.on_radar_health({"interference": True})
        assert events == [("radar_health", {"interference": True})]

    def test_flag_reaches_start_monitor(self, monkeypatch):
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
        monkeypatch.setattr(server_module, "start_monitor", lambda **kw: calls.append(kw))
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "install_signal_handlers", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *_a, **_kw: None)
        base_argv = ["openflight-server", "--mock", "--no-logging"]

        monkeypatch.setattr(sys, "argv", base_argv)
        server_module.main()
        assert calls[-1]["interference_check"] is False

        monkeypatch.setattr(sys, "argv", [*base_argv, "--interference-check"])
        server_module.main()
        assert calls[-1]["interference_check"] is True
