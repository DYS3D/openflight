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
