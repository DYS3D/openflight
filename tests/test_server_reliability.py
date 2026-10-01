"""Concurrency and resource-lifetime tests for server-side shared state."""

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.ops243 import Direction


@pytest.fixture
def debug_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(server_module, "DEBUG_LOG_DIR", tmp_path / "openflight_logs")
    yield tmp_path
    server_module.stop_debug_logging()


def _raw_handlers():
    return [
        handler
        for handler in logging.getLogger("ops243.raw").handlers
        if handler is server_module._debug_raw_handler
    ]


def _reading(speed=100.0):
    return SimpleNamespace(speed=speed, direction=Direction.OUTBOUND, magnitude=50, unit="mph")


class TestDebugLogging:
    def test_restart_closes_the_previous_file_and_handler(self, debug_home):
        server_module.start_debug_logging()
        first_file = server_module.debug_log_file
        first_handler = server_module._debug_raw_handler

        time.sleep(0.001)
        server_module.start_debug_logging()

        assert first_file.closed
        assert first_handler not in logging.getLogger("ops243.raw").handlers
        assert first_handler not in logging.getLogger("ops243").handlers
        assert len(_raw_handlers()) == 1

    def test_stop_closes_everything(self, debug_home):
        server_module.start_debug_logging()
        log_file = server_module.debug_log_file
        handler = server_module._debug_raw_handler

        server_module.stop_debug_logging()

        assert log_file.closed
        assert server_module.debug_log_file is None
        assert server_module._debug_raw_handler is None
        assert handler not in logging.getLogger("ops243").handlers

    def test_readings_race_with_stop_without_errors(self, debug_home):
        errors = []
        stop = threading.Event()

        def writer():
            while not stop.is_set():
                try:
                    server_module.log_debug_reading(_reading())
                except Exception as error:  # pragma: no cover - the regression
                    errors.append(error)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(20):
                server_module.start_debug_logging()
                server_module.stop_debug_logging()
        finally:
            stop.set()
            thread.join()

        assert errors == []

    def test_readings_are_dropped_when_logging_is_off(self, debug_home):
        server_module.stop_debug_logging()
        assert server_module._write_debug_entry({"type": "reading"}) is False

    def test_toggle_twice_leaves_no_open_file(self, debug_home, monkeypatch):
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *_a, **_kw: None)
        server_module.handle_toggle_debug()
        log_file = server_module.debug_log_file
        server_module.handle_toggle_debug()
        assert server_module.debug_mode is False
        assert log_file.closed


class TestRadarConfigConcurrency:
    def test_concurrent_updates_keep_radar_and_config_in_agreement(self, monkeypatch):
        applied = {}

        class SlowRadar:
            def set_min_speed_filter(self, value):
                time.sleep(0.002)
                applied["min_speed"] = value

            def set_max_speed_filter(self, value):
                # Earlier requests finish last, so unserialized updates interleave.
                time.sleep((158 - value) * 0.003)
                applied["max_speed"] = value

        monkeypatch.setattr(server_module, "monitor", SimpleNamespace(radar=SlowRadar()))
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 10, "max_speed": 220})
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *_a, **_kw: None)

        requests = [{"min_speed": 20 + i, "max_speed": 150 + i} for i in range(8)]
        threads = [
            threading.Thread(target=server_module.handle_set_radar_config, args=(request,))
            for request in requests
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        final = server_module.radar_config
        assert final == applied
        assert final["max_speed"] - final["min_speed"] == 130


class TestExplicitDebugToggle:
    @pytest.fixture(autouse=True)
    def _quiet(self, debug_home, monkeypatch):
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *_a, **_kw: None)

    def test_explicit_enable_and_disable(self):
        server_module.handle_toggle_debug({"enabled": True})
        assert server_module.debug_mode is True
        log_file = server_module.debug_log_file
        server_module.handle_toggle_debug({"enabled": False})
        assert server_module.debug_mode is False
        assert log_file.closed

    def test_repeated_enable_is_idempotent(self):
        server_module.handle_toggle_debug({"enabled": True})
        first = server_module.debug_log_file
        server_module.handle_toggle_debug({"enabled": True})
        assert server_module.debug_log_file is first
        assert not first.closed

    def test_bare_event_still_toggles(self):
        server_module.handle_toggle_debug()
        assert server_module.debug_mode is True
        server_module.handle_toggle_debug(None)
        assert server_module.debug_mode is False

    @pytest.mark.parametrize("payload", [{}, {"enabled": "yes"}, "on", 1, ["enabled"]])
    def test_non_boolean_payloads_fall_back_to_toggle(self, payload):
        server_module.handle_toggle_debug(payload)
        assert server_module.debug_mode is True


class TestMonitorSwapLocking:
    def test_start_monitor_publishes_state_under_the_config_lock(self, monkeypatch):
        seen = []

        class Probe:
            def connect(self):
                pass

            def start(self, **_kw):
                pass

            def stop(self):
                pass

            def disconnect(self):
                pass

            def get_session_stats(self):
                return {}

        monkeypatch.setattr(server_module, "MockLaunchMonitor", Probe)
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        real_lock = server_module._config_lock

        class SpyLock:
            def __enter__(self):
                real_lock.__enter__()
                seen.append("enter")

            def __exit__(self, *args):
                seen.append(("exit", server_module.monitor is not None, server_module.mock_mode))
                return real_lock.__exit__(*args)

        monkeypatch.setattr(server_module, "_config_lock", SpyLock())
        try:
            server_module.start_monitor(mock=True)
            assert ("exit", True, True) in seen
            seen.clear()
            server_module.stop_monitor()
            assert server_module.monitor is None
            assert seen and seen[0] == "enter"
        finally:
            monkeypatch.setattr(server_module, "_config_lock", real_lock)
            server_module.monitor = None

    def test_stop_monitor_does_not_hold_the_lock_while_stopping(self, monkeypatch):
        """stop() may join a thread that needs the lock, so it must run unlocked."""
        held_during_stop = []

        class Probe:
            def stop(self):
                acquired = server_module._config_lock.acquire(blocking=False)
                held_during_stop.append(acquired)
                if acquired:
                    server_module._config_lock.release()

            def disconnect(self):
                pass

        monkeypatch.setattr(server_module, "monitor", Probe())
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        # RLock re-acquire by the same thread would succeed either way, so
        # probe from a second thread.
        result = []

        def worker():
            result.append(server_module._config_lock.acquire(timeout=1.0))
            server_module._config_lock.release()

        class ThreadProbe(Probe):
            def stop(self):
                t = threading.Thread(target=worker)
                t.start()
                t.join()

        monkeypatch.setattr(server_module, "monitor", ThreadProbe())
        server_module.stop_monitor()
        assert result == [True]
        assert server_module.monitor is None


class TestSetClubPayload:
    @pytest.mark.parametrize("payload", [None, "driver", 7, [], {"club": "not-a-club"}])
    def test_bad_payloads_do_not_raise_or_change_the_club(self, monkeypatch, payload):
        emitted = []
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **_kw: emitted.append(a))
        server_module.handle_set_club(payload)
        if isinstance(payload, dict):
            assert [event for event, *_rest in emitted] == ["club_error"]
        else:
            assert emitted == [("club_changed", {"club": "driver"})]


class TestStartupRetentionCoversDebugLogs:
    def test_prune_session_logs_also_prunes_the_debug_dir(self, debug_home, monkeypatch):
        import os

        sessions = debug_home / "openflight_sessions"
        sessions.mkdir()
        debug_dir = server_module.DEBUG_LOG_DIR
        debug_dir.mkdir()
        old_debug = debug_dir / "debug_20200101_000000_000000.jsonl"
        old_debug.write_text("{}")
        os.utime(old_debug, (1_000_000, 1_000_000))
        monkeypatch.setattr("openflight.cloud.config.load_config", lambda: None)

        server_module._prune_session_logs(sessions, max_age_days=30, max_total_mb=0)

        assert not old_debug.exists()


class TestSigtermShutdown:
    def test_sigterm_runs_the_cleanup_path(self, monkeypatch):
        import signal

        calls = []
        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target, args))

            def start(self):
                pass

        monkeypatch.setattr(server_module.threading, "Thread", FakeThread)
        monkeypatch.setattr(
            server_module,
            "_cleanup_hardware_for_shutdown",
            lambda: calls.append("cleanup") or False,
        )

        server_module._handle_termination_signal(signal.SIGTERM, None)

        assert started and started[0][0] is server_module._shutdown_process_after_delay
        started[0][0](*started[0][1])
        assert calls == ["cleanup"]

    def test_sigterm_during_an_update_lets_it_roll_back_instead_of_exiting(self, monkeypatch):
        import signal
        from types import SimpleNamespace

        started = []
        monkeypatch.setattr(
            server_module.threading,
            "Thread",
            lambda **kwargs: SimpleNamespace(start=lambda: started.append(kwargs)),
        )
        monkeypatch.setattr(
            server_module, "update_service", SimpleNamespace(request_stop_apply=lambda: True)
        )
        server_module._handle_termination_signal(signal.SIGTERM, None)
        assert started == []

        response = server_module.app.test_client().post(
            "/api/shutdown", environ_base={"REMOTE_ADDR": "127.0.0.1"}
        )
        assert response.status_code == 202
        assert response.get_json() == {"status": "updating"}
        assert started == []

    def test_install_signal_handlers_registers_sigterm(self, monkeypatch):
        import signal

        registered = {}
        monkeypatch.setattr(
            server_module.signal, "signal", lambda num, fn: registered.update({num: fn})
        )
        server_module.install_signal_handlers()
        assert registered[signal.SIGTERM] is server_module._handle_termination_signal

    def test_install_signal_handlers_tolerates_non_main_thread(self, monkeypatch):
        def refuse(*_a):
            raise ValueError("signal only works in main thread")

        monkeypatch.setattr(server_module.signal, "signal", refuse)
        server_module.install_signal_handlers()  # must not raise
