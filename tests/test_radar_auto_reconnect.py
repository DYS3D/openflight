"""``--radar-auto-reconnect``: serial-error recovery for the OPS243 and IWR6843.

Every test here drives the real capture loops with fake serial transports:
no radar hardware is involved.
"""

from __future__ import annotations

import logging
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import serial

from openflight import server as server_module
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.ops243 import OPS243Radar
from openflight.radar_reconnect import (
    RADAR_STATE_CONNECTED,
    RADAR_STATE_RECONNECTING,
    RECONNECT_BACKOFF_MAX_S,
    reconnect_backoff_s,
)
from openflight.rolling_buffer import RollingBufferMonitor, monitor as monitor_module

STABLE_OPS_PORT = OPS243Radar.STABLE_PORT


class TestBackoffSchedule:
    def test_doubles_from_one_second_and_caps_at_thirty(self):
        delays = [reconnect_backoff_s(attempt) for attempt in range(1, 9)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
        assert max(delays) == RECONNECT_BACKOFF_MAX_S

    def test_zero_failures_waits_the_initial_delay(self):
        assert reconnect_backoff_s(0) == 1.0


# --- OPS243 rolling-buffer monitor ---


class FakeOpsSerial:
    """pyserial double: ``in_waiting`` raises SerialException ``failures`` times.

    Once the failures are spent, ``on_idle`` runs on the next poll so a test
    can stop the monitor instead of sitting through the 30 s trigger wait.
    """

    def __init__(self, log: list, *, failures: int, on_idle=None):
        self.log = log
        self.failures = failures
        self.on_idle = on_idle
        self.is_open = True
        self.timeout = 1.0
        self.polls = 0

    @property
    def in_waiting(self) -> int:
        self.polls += 1
        if self.failures > 0:
            self.failures -= 1
            raise serial.SerialException("read failed: [Errno 5] Input/output error")
        if self.on_idle is not None:
            self.on_idle()
        return 0

    def read(self, _size: int) -> bytes:
        return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.log.append("close")
        self.is_open = False


class OpsHarness:
    """A RollingBufferMonitor with a real OPS243Radar on a fake serial port."""

    def __init__(
        self,
        monkeypatch,
        *,
        auto_reconnect: bool,
        read_failures: int,
        detection_failures: int = 0,
        port: str | None = None,
        trigger_type: str = "sound",
    ):
        self.log: list = []
        self.callbacks: list[str] = []
        self.delays: list[float] = []
        self.detection_calls = 0
        self.sleeps: list[float] = []
        self.session_errors: list = []
        self.stop_during_backoff = False

        self.monitor = RollingBufferMonitor(
            port=port,
            trigger_type=trigger_type,
            radar_auto_reconnect=auto_reconnect,
        )
        self.monitor._radar_status_callback = self.callbacks.append
        self.monitor._diagnostic_callback = None
        radar = self.monitor.radar
        radar.port = port or STABLE_OPS_PORT
        radar.serial = FakeOpsSerial(self.log, failures=read_failures, on_idle=self.stop)

        def find_radar_ports():
            self.detection_calls += 1
            if self.detection_calls <= detection_failures:
                return []
            return [STABLE_OPS_PORT]

        def open_serial(_radar, _baud, _timeout=1.0):
            self.log.append(f"open:{radar.port}")
            radar.serial = FakeOpsSerial(self.log, failures=0, on_idle=self.stop)

        def prepare(_radar, pre_trigger_segments, sample_rate_ksps):
            self.log.append(f"configure:S#{pre_trigger_segments}:{sample_rate_ksps}ksps")

        def wait(delay):
            self.delays.append(delay)
            if self.stop_during_backoff:
                self.stop()
                return True
            return False

        monkeypatch.setattr(OPS243Radar, "find_radar_ports", staticmethod(find_radar_ports))
        monkeypatch.setattr(OPS243Radar, "_open_serial", open_serial)
        monkeypatch.setattr(OPS243Radar, "_should_negotiate_baud", lambda _radar: False)
        monkeypatch.setattr(OPS243Radar, "_log_transport", lambda _radar: None)
        monkeypatch.setattr(OPS243Radar, "_drain_serial", lambda _radar, **_kw: None)
        monkeypatch.setattr(OPS243Radar, "prepare_persisted_rolling_buffer", prepare)
        monkeypatch.setattr(self.monitor._stop_event, "wait", wait)
        monkeypatch.setattr(monitor_module.time, "sleep", self.sleeps.append)
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            monitor_module,
            "log_session_error",
            lambda message, **kwargs: self.session_errors.append((message, kwargs)),
        )

    def stop(self) -> None:
        self.monitor._running = False
        self.monitor._stop_event.set()

    def run(self) -> None:
        self.monitor._running = True
        self.monitor._capture_loop()


def _monitor_records(caplog, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "openflight.rolling_buffer.monitor" and record.levelno == level
    ]


class TestOps243AutoReconnect:
    def test_serial_error_closes_reopens_reconfigures_and_reports_state(self, monkeypatch, caplog):
        harness = OpsHarness(
            monkeypatch, auto_reconnect=True, read_failures=1, detection_failures=7
        )

        with caplog.at_level(logging.DEBUG, logger="openflight.rolling_buffer.monitor"):
            harness.run()

        assert harness.log == [
            "close",
            f"open:{STABLE_OPS_PORT}",
            "configure:S#12:30ksps",
        ]
        assert harness.callbacks == [RADAR_STATE_RECONNECTING, RADAR_STATE_CONNECTED]
        assert harness.monitor.radar_state == RADAR_STATE_CONNECTED
        assert harness.monitor.radar.port == STABLE_OPS_PORT
        # Seven empty detections, then the stable udev name answers.
        assert harness.detection_calls == 8
        assert harness.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
        assert harness.sleeps == []

        errors = _monitor_records(caplog, logging.ERROR)
        assert len(errors) == 1 and "reconnecting" in errors[0]
        infos = [m for m in _monitor_records(caplog, logging.INFO) if "reconnected" in m]
        assert infos == [
            f"[MONITOR] Radar reconnected on {STABLE_OPS_PORT} after 7 failed attempt(s) in 0s"
        ]
        assert [message for message, _ in harness.session_errors] == [
            "Radar serial link lost; reconnecting"
        ]

    def test_pinned_port_is_reopened_without_usb_detection(self, monkeypatch):
        harness = OpsHarness(monkeypatch, auto_reconnect=True, read_failures=1, port="/dev/ttyAMA0")

        harness.run()

        assert harness.detection_calls == 0
        assert harness.log == ["close", "open:/dev/ttyAMA0", "configure:S#12:30ksps"]
        assert harness.monitor.radar.port == "/dev/ttyAMA0"

    def test_speed_trigger_is_marked_for_reconfiguration_instead(self, monkeypatch):
        harness = OpsHarness(
            monkeypatch, auto_reconnect=True, read_failures=0, trigger_type="speed"
        )
        trigger = harness.monitor.trigger
        trigger._needs_reconfigure = False
        calls = {"n": 0}

        def wait_for_trigger(**_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise serial.SerialException("gone")
            harness.stop()
            return None

        monkeypatch.setattr(trigger, "wait_for_trigger", wait_for_trigger)

        harness.run()

        assert harness.log == ["close", f"open:{STABLE_OPS_PORT}"]
        assert trigger._needs_reconfigure is True
        assert harness.callbacks == [RADAR_STATE_RECONNECTING, RADAR_STATE_CONNECTED]

    def test_stop_during_backoff_ends_the_reconnect_loop(self, monkeypatch):
        harness = OpsHarness(
            monkeypatch, auto_reconnect=True, read_failures=1, detection_failures=99
        )
        harness.stop_during_backoff = True

        harness.run()

        assert harness.log == ["close"]
        assert harness.delays == [1.0]
        assert harness.callbacks == [RADAR_STATE_RECONNECTING]
        assert harness.monitor.radar_state == RADAR_STATE_RECONNECTING

    def test_trigger_timeout_does_not_reconnect(self, monkeypatch, caplog):
        harness = OpsHarness(monkeypatch, auto_reconnect=True, read_failures=0)
        radar = harness.monitor.radar
        # Poll once, then let the wall clock jump past the 30 s trigger window
        # so wait_for_hardware_trigger returns an empty read (a real timeout).
        clock = {"now": 1_000.0}

        def fake_time():
            clock["now"] += 31.0
            return clock["now"]

        radar.serial.on_idle = None
        polls = {"n": 0}

        def reset_input_buffer():
            polls["n"] += 1
            if polls["n"] == 2:
                harness.stop()

        radar.serial.reset_input_buffer = reset_input_buffer
        with patch("openflight.ops243.time.time", side_effect=fake_time):
            with caplog.at_level(logging.INFO):
                harness.run()

        assert "no data received within 30s" in caplog.text
        assert harness.log == []
        assert harness.callbacks == []
        assert harness.detection_calls == 0
        assert harness.monitor.radar_state == RADAR_STATE_CONNECTED

    def test_non_serial_errors_keep_the_legacy_path(self, monkeypatch, caplog):
        harness = OpsHarness(monkeypatch, auto_reconnect=True, read_failures=0)
        calls = {"n": 0}

        def wait_for_trigger(**_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad capture")
            harness.stop()
            return None

        monkeypatch.setattr(harness.monitor.trigger, "wait_for_trigger", wait_for_trigger)

        with caplog.at_level(logging.ERROR):
            harness.run()

        assert harness.log == []
        assert harness.callbacks == []
        assert harness.sleeps == [1.0]
        assert [m for m in _monitor_records(caplog, logging.ERROR)] == [
            "[MONITOR] Capture loop error: bad capture"
        ]


class TestOps243AutoReconnectOff:
    """The default must keep today's loop: log, sleep 1 s, retry the same port."""

    def test_flag_off_by_default(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        assert monitor.radar_auto_reconnect is False
        assert monitor.radar_state == RADAR_STATE_CONNECTED

    def test_serial_errors_are_retried_on_the_dead_port(self, monkeypatch, caplog):
        harness = OpsHarness(
            monkeypatch, auto_reconnect=False, read_failures=3, detection_failures=0
        )
        original_serial = harness.monitor.radar.serial

        with caplog.at_level(logging.ERROR, logger="openflight.rolling_buffer.monitor"):
            harness.run()

        assert harness.log == []
        assert harness.monitor.radar.serial is original_serial
        assert harness.detection_calls == 0
        assert harness.delays == []
        assert harness.sleeps == [1.0, 1.0, 1.0]
        assert harness.callbacks == []
        assert harness.monitor.radar_state == RADAR_STATE_CONNECTED
        assert (
            _monitor_records(caplog, logging.ERROR)
            == ["[MONITOR] Capture loop error: read failed: [Errno 5] Input/output error"] * 3
        )
        assert [message for message, _ in harness.session_errors] == [
            "Rolling buffer capture loop error"
        ] * 3


# --- IWR6843 capture monitor ---


def _raw_dump() -> bytes:
    cube = np.zeros((2, 4, 4, 8), dtype=complex)
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)


class FakeIwrRadar:
    def __init__(self, log: list, name: str, *, dump_errors: list[Exception] | None = None):
        self.log = log
        self.name = name
        self.port = f"/dev/{name}"
        self.dump_errors = list(dump_errors or [])
        self.configs: list[str] = []
        self.closed = False

    def send_config(self, path: str) -> None:
        self.log.append(f"{self.name}:send_config")
        self.configs.append(path)

    def read_dump(self) -> bytes:
        self.log.append(f"{self.name}:read_dump")
        if self.dump_errors:
            raise self.dump_errors.pop(0)
        return _raw_dump()

    def stop_sensor(self) -> None:
        self.log.append(f"{self.name}:sensorStop")

    def close(self) -> None:
        self.log.append(f"{self.name}:close")
        self.closed = True


class FakeButton:
    def __init__(self, pin, pull_up, bounce_time):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None

    def close(self):
        pass


class IwrHarness:
    def __init__(
        self,
        tmp_path,
        monkeypatch,
        *,
        auto_reconnect: bool,
        dump_errors: list[Exception],
        detection_failures: int = 0,
        port: str | None = None,
    ):
        self.config = tmp_path / "radar.cfg"
        self.config.write_text("sensorStart\n", encoding="utf-8")
        self.log: list = []
        self.callbacks: list[str] = []
        self.delays: list[float] = []
        self.factory_ports: list[str | None] = []
        self.replacement: FakeIwrRadar | None = None
        self.original = FakeIwrRadar(self.log, "usb0", dump_errors=dump_errors)

        def radar_factory(requested_port):
            self.factory_ports.append(requested_port)
            if len(self.factory_ports) <= detection_failures:
                raise RuntimeError("no IWR6843 CLI found — board on, flashed, single-port fw?")
            self.replacement = FakeIwrRadar(self.log, "usb1")
            return self.replacement

        self.monitor = IWR6843CaptureMonitor(
            config_path=self.config,
            output_dir=tmp_path / "dumps",
            port=port,
            radar=self.original,
            button_factory=FakeButton,
            radar_auto_reconnect=auto_reconnect,
            radar_status_callback=self.callbacks.append,
            radar_factory=radar_factory,
        )

        def wait(delay):
            self.delays.append(delay)
            return False

        monkeypatch.setattr(self.monitor._stop_event, "wait", wait)

    def wait_for_reconnect(self, timeout_s: float = 2.0) -> None:
        """Block until the worker has reported reconnecting and then connected.

        The callback list only grows, so it cannot miss a transition the way
        polling ``radar_state`` could when the worker flips it twice quickly.
        """
        expected = [RADAR_STATE_RECONNECTING, RADAR_STATE_CONNECTED]
        deadline = time.monotonic() + timeout_s
        while self.callbacks != expected:
            if time.monotonic() > deadline:
                raise AssertionError(f"radar status callbacks stayed {self.callbacks!r}")
            time.sleep(0.005)


class TestIwr6843AutoReconnect:
    def test_serial_error_reopens_via_detection_and_resends_config(
        self, tmp_path, monkeypatch, caplog
    ):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=True,
            dump_errors=[serial.SerialException("device disconnected")],
            detection_failures=6,
        )
        monitor = harness.monitor
        with caplog.at_level(logging.DEBUG, logger="openflight.iwr6843.monitor"):
            monitor.start()
            edge = time.time()
            assert monitor.notify_trigger(edge)
            failed = monitor.capture_for_shot(edge, timeout_s=2.0)
            assert failed is not None and not failed.valid
            assert failed.error == "device disconnected"

            harness.wait_for_reconnect()

            time.sleep(0.11)  # clear the 100 ms edge debounce
            edge = time.time()
            assert monitor.notify_trigger(edge)
            recovered = monitor.capture_for_shot(edge, timeout_s=2.0)
            assert recovered is not None and recovered.valid
            monitor.stop()

        assert harness.original.closed
        assert harness.factory_ports == [None] * 7
        assert harness.replacement is not None
        assert monitor.radar is harness.replacement
        assert harness.replacement.configs == [str(harness.config)]
        assert harness.log[:4] == [
            "usb0:send_config",
            "usb0:read_dump",
            "usb0:close",
            "usb1:send_config",
        ]
        assert "usb1:read_dump" in harness.log
        assert harness.callbacks == [RADAR_STATE_RECONNECTING, RADAR_STATE_CONNECTED]
        assert harness.delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]

        records = [r for r in caplog.records if r.name == "openflight.iwr6843.monitor"]
        errors = [r.getMessage() for r in records if r.levelno == logging.ERROR]
        assert errors == [
            "[IWR6843] Serial link lost on /dev/usb0 (device disconnected); reconnecting"
        ]
        warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
        assert not any("Capture #1 failed" in message for message in warnings)
        assert any(
            "Reconnected on /dev/usb1 after 6 failed attempt(s)" in r.getMessage() for r in records
        )

    def test_pinned_port_skips_detection(self, tmp_path, monkeypatch):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=True,
            dump_errors=[OSError(5, "Input/output error")],
            port="/dev/ttyUSB3",
        )
        monitor = harness.monitor
        monitor.start()
        edge = time.time()
        monitor.notify_trigger(edge)
        monitor.capture_for_shot(edge, timeout_s=2.0)
        harness.wait_for_reconnect()
        monitor.stop()

        assert harness.factory_ports == ["/dev/ttyUSB3"]

    def test_config_rejection_after_reopen_retries_with_backoff(self, tmp_path, monkeypatch):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=True,
            dump_errors=[serial.SerialException("device disconnected")],
        )
        attempts = {"n": 0}

        def flaky_factory(_port):
            attempts["n"] += 1
            radar = FakeIwrRadar(harness.log, f"try{attempts['n']}")
            if attempts["n"] == 1:
                monkeypatch.setattr(
                    radar,
                    "send_config",
                    lambda _path: (_ for _ in ()).throw(RuntimeError("config rejected")),
                )
            harness.replacement = radar
            return radar

        harness.monitor._radar_factory = flaky_factory
        monitor = harness.monitor
        monitor.start()
        edge = time.time()
        monitor.notify_trigger(edge)
        monitor.capture_for_shot(edge, timeout_s=2.0)
        harness.wait_for_reconnect()
        monitor.stop()

        assert "try1:close" in harness.log
        assert harness.delays == [1.0]
        assert monitor.radar.name == "try2"

    def test_edges_are_ignored_while_reconnecting(self, tmp_path, monkeypatch):
        harness = IwrHarness(tmp_path, monkeypatch, auto_reconnect=True, dump_errors=[])
        monitor = harness.monitor
        monitor.start()
        monitor.radar_state = RADAR_STATE_RECONNECTING

        assert monitor.notify_trigger(time.time()) is False
        assert monitor._events.empty()
        monitor.stop()

    def test_short_dump_is_a_capture_failure_not_a_link_loss(self, tmp_path, monkeypatch, caplog):
        harness = IwrHarness(tmp_path, monkeypatch, auto_reconnect=True, dump_errors=[])
        harness.original.read_dump = lambda: b"ILD1"  # stalled/timed-out transfer
        monitor = harness.monitor
        with caplog.at_level(logging.WARNING, logger="openflight.iwr6843.monitor"):
            monitor.start()
            edge = time.time()
            monitor.notify_trigger(edge)
            capture = monitor.capture_for_shot(edge, timeout_s=2.0)
            monitor.stop()

        assert capture is not None and not capture.valid
        assert "short IWR6843 dump" in capture.error
        assert harness.factory_ports == []
        assert harness.callbacks == []
        assert monitor.radar_state == RADAR_STATE_CONNECTED
        assert "Capture #1 failed" in caplog.text

    def test_stop_during_backoff_ends_the_reconnect_loop(self, tmp_path, monkeypatch):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=True,
            dump_errors=[serial.SerialException("device disconnected")],
            detection_failures=99,
        )
        monitor = harness.monitor

        def wait(delay):
            harness.delays.append(delay)
            monitor._running = False
            return True

        monkeypatch.setattr(monitor._stop_event, "wait", wait)
        monitor.start()
        edge = time.time()
        monitor.notify_trigger(edge)
        monitor.capture_for_shot(edge, timeout_s=2.0)
        monitor._worker.join(timeout=2.0)

        assert not monitor._worker.is_alive()
        assert harness.delays == [1.0]
        assert harness.factory_ports == [None]


    def test_stop_during_reopen_closes_the_new_radar(self, tmp_path, monkeypatch):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=True,
            dump_errors=[serial.SerialException("device disconnected")],
        )
        monitor = harness.monitor
        real_send_config = FakeIwrRadar.send_config

        def send_config_while_stopping(radar, path):
            real_send_config(radar, path)
            if radar is harness.replacement:
                # stop() gave up joining the worker while this config was in flight.
                monitor._running = False

        monkeypatch.setattr(FakeIwrRadar, "send_config", send_config_while_stopping)
        monitor.start()
        edge = time.time()
        monitor.notify_trigger(edge)
        monitor.capture_for_shot(edge, timeout_s=2.0)
        monitor._worker.join(timeout=2.0)

        assert not monitor._worker.is_alive()
        assert harness.replacement is not None
        assert harness.replacement.closed
        assert monitor.radar is not harness.replacement


class TestIwr6843AutoReconnectOff:
    def test_flag_off_by_default(self, tmp_path):
        monitor = IWR6843CaptureMonitor(
            config_path=tmp_path / "radar.cfg",
            output_dir=tmp_path,
            radar=FakeIwrRadar([], "usb0"),
            button_factory=FakeButton,
        )
        assert monitor.radar_auto_reconnect is False
        assert monitor.radar_state == RADAR_STATE_CONNECTED

    def test_serial_error_is_only_a_failed_capture(self, tmp_path, monkeypatch, caplog):
        harness = IwrHarness(
            tmp_path,
            monkeypatch,
            auto_reconnect=False,
            dump_errors=[serial.SerialException("device disconnected")] * 2,
        )
        monitor = harness.monitor
        with caplog.at_level(logging.WARNING, logger="openflight.iwr6843.monitor"):
            monitor.start()
            for _ in range(2):
                edge = time.time()
                assert monitor.notify_trigger(edge)
                capture = monitor.capture_for_shot(edge, timeout_s=2.0)
                assert capture is not None and capture.error == "device disconnected"
                time.sleep(0.11)
            monitor.stop()

        assert harness.log[-2:] == ["usb0:sensorStop", "usb0:close"]  # shutdown only
        assert harness.factory_ports == []
        assert harness.callbacks == []
        assert harness.delays == []
        assert monitor.radar_state == RADAR_STATE_CONNECTED
        assert caplog.text.count("failed: device disconnected") == 2


# --- Server plumbing ---


class TestServerTriggerStatus:
    @pytest.fixture
    def rolling_buffer_monitor(self, monkeypatch):
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.radar.port = STABLE_OPS_PORT
        monkeypatch.setattr(server_module, "monitor", monitor)
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        return monitor

    def test_reports_connected_state_by_default(self, rolling_buffer_monitor):
        status = server_module._get_trigger_status()

        assert status["radar_connected"] is True
        assert status["radar_state"] == "connected"
        assert status["radar_port"] == STABLE_OPS_PORT
        assert status["iwr6843_state"] is None

    def test_reconnecting_monitor_reports_radar_disconnected(
        self, rolling_buffer_monitor, monkeypatch
    ):
        rolling_buffer_monitor.radar_state = RADAR_STATE_RECONNECTING
        monkeypatch.setattr(
            server_module,
            "iwr6843_runtime",
            SimpleNamespace(capture_monitor=SimpleNamespace(radar_state="reconnecting")),
        )

        status = server_module._get_trigger_status()

        assert status["radar_connected"] is False
        assert status["radar_state"] == "reconnecting"
        assert status["iwr6843_state"] == "reconnecting"

    def test_mock_mode_reports_disconnected_state(self, monkeypatch):
        monkeypatch.setattr(server_module, "monitor", object())
        monkeypatch.setattr(server_module, "mock_mode", True)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        status = server_module._get_trigger_status()

        assert status["mode"] == "mock"
        assert status["radar_connected"] is False
        assert status["radar_state"] == "disconnected"

    def test_radar_status_callback_pushes_trigger_status(self, rolling_buffer_monitor, monkeypatch):
        emitted = []
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda event, payload: emitted.append((event, payload))
        )
        rolling_buffer_monitor.radar_state = RADAR_STATE_RECONNECTING

        server_module.on_radar_status("reconnecting")

        assert [event for event, _ in emitted] == ["trigger_status"]
        assert emitted[0][1]["radar_state"] == "reconnecting"
        assert emitted[0][1]["radar_connected"] is False

    def test_session_start_config_records_the_flag(self, monkeypatch):
        monkeypatch.setattr(server_module, "radar_auto_reconnect_enabled", True)
        assert server_module._session_start_config()["radar_auto_reconnect"] is True
        monkeypatch.setattr(server_module, "radar_auto_reconnect_enabled", False)
        assert server_module._session_start_config()["radar_auto_reconnect"] is False


class TestServerCliFlag:
    # Module state main() assigns from argparse; restored so later test modules
    # (test_server.py runs after this one) keep their import-time defaults.
    MAIN_GLOBALS = (
        "ballistics_enabled",
        "air_density",
        "battery_provider",
        "profile_store",
        "ball_speed_correction_enabled",
        "ball_speed_correction_distance_ft",
        "ball_speed_correction_ball_above_radar_ft",
        "_VERTICAL_RADAR_GATE_BYPASS",
        "calculated_spin_enabled",
        "radar_auto_reconnect_enabled",
        "sim_connectors",
    )

    @pytest.mark.parametrize(
        ("extra_argv", "expected"), [([], False), (["--radar-auto-reconnect"], True)]
    )
    def test_flag_reaches_both_radar_monitors(self, monkeypatch, extra_argv, expected):
        for name in self.MAIN_GLOBALS:
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--no-logging", "--iwr6843", *extra_argv],
        )
        received = {}

        def fake_init_iwr6843(**kwargs):
            received["iwr6843"] = kwargs["radar_auto_reconnect"]
            monkeypatch.setattr(
                server_module,
                "iwr6843_runtime",
                SimpleNamespace(
                    calibration=SimpleNamespace(tee_ball_height_m=0.04, radar_height_m=0.3),
                    tx_order="normal",
                ),
            )
            return True

        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kwargs: None)
        monkeypatch.setattr(server_module, "init_iwr6843", fake_init_iwr6843)
        monkeypatch.setattr(
            server_module,
            "start_monitor",
            lambda **kwargs: received.setdefault("ops", kwargs["radar_auto_reconnect"]),
        )
        monkeypatch.setattr(server_module.socketio, "run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "_prune_session_logs", lambda *_a, **_k: None)

        server_module.main()

        assert received == {"iwr6843": expected, "ops": expected}
        assert server_module.radar_auto_reconnect_enabled is expected
