"""Tests for the IWR6843 CLI and dump serial contract."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843.driver import DumpRestartError, IWR6843Radar
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TEMP_REPORT_KEYS,
    pack_dump,
)
from openflight.iwr6843.readback import encode_summary_reply, summarize_capture


def test_send_config_rejects_missing_cli_acknowledgement(tmp_path, monkeypatch):
    """A wedged board must not be reported as configured and armed."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)
    monkeypatch.setattr(radar, "cmd", lambda *_args, **_kwargs: "")

    with pytest.raises(RuntimeError, match="did not acknowledge"):
        radar.send_config(str(config))


def test_stop_sensor_requires_acknowledgement_and_inactive_health(monkeypatch):
    """Shutdown must leave firmware idle rather than merely close the host UART."""
    radar = IWR6843Radar.__new__(IWR6843Radar)
    responses = iter(["sensorStop\nDone\nl3dump:/>", "stats\nactive=0\nDone\nl3dump:/>"])
    calls = []

    def fake_cmd(command, window):
        calls.append((command, window))
        return next(responses)

    monkeypatch.setattr(radar, "cmd", fake_cmd)

    radar.stop_sensor()

    assert calls == [("sensorStop", 3.0), ("stats", 2.0)]


def test_stop_sensor_rejects_firmware_that_remains_active(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    responses = iter(["sensorStop\nDone\n", "stats\nactive=1\nDone\n"])
    monkeypatch.setattr(radar, "cmd", lambda *_args: next(responses))

    with pytest.raises(RuntimeError, match="remained active"):
        radar.stop_sensor()


def test_send_config_flushes_previous_mmwave_profile_when_config_omits_flush(tmp_path, monkeypatch):
    """Repeated startup must not exhaust the firmware's mmWave profile slots."""
    config = tmp_path / "radar.cfg"
    config.write_text("dfeDataOutputMode 1\nsensorStart\n", encoding="utf-8")
    commands = []
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)

    def command(line, *_args, **_kwargs):
        commands.append(line)
        if line == "stats":
            return "active=1\nDone\n"
        return "Done\n"

    monkeypatch.setattr(radar, "cmd", command)

    radar.send_config(str(config))

    assert commands == [
        "sensorStop",
        "flushCfg",
        "dfeDataOutputMode 1",
        "sensorStart",
        "stats",
    ]


def test_send_config_waits_for_sensor_to_become_active(tmp_path, monkeypatch):
    """sensorStart may acknowledge before RF calibration and HWA startup finish."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    statuses = iter(
        (
            "active=0 calib=0x0 hwa_frames=0\nDone\n",
            "active=0 calib=0x1ffe hwa_frames=0\nDone\n",
            "active=1 calib=0x1ffe hwa_frames=2\nDone\n",
        )
    )
    commands = []
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)

    def command(line, *_args, **_kwargs):
        commands.append(line)
        return next(statuses) if line == "stats" else "Done\n"

    monkeypatch.setattr(radar, "cmd", command)

    radar.send_config(str(config))

    assert commands == ["sensorStop", "flushCfg", "sensorStart", "stats", "stats", "stats"]


class FakeSerial:
    """Serial double that exposes the in_waiting/read/write pieces read_dump uses."""

    def __init__(self, payload: bytes):
        self.payload = bytearray(payload)
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.payload)

    def reset_input_buffer(self):
        pass

    def write(self, data: bytes):
        self.writes.append(data)

    def read(self, nbytes: int):
        nbytes = min(nbytes, len(self.payload))
        chunk = self.payload[:nbytes]
        del self.payload[:nbytes]
        return bytes(chunk)


def test_read_dump_waits_for_cli_ready_after_binary_payload():
    raw = pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)

    class ChunkedSerial:
        def __init__(self):
            self.chunks = [bytearray(b"l3dump\r\n" + raw), bytearray(b"Done\r\nl3dump:/>")]
            self.writes = []
            self.delay_next_chunk = False

        @property
        def in_waiting(self):
            if self.delay_next_chunk:
                return 0
            return len(self.chunks[0]) if self.chunks else 0

        def reset_input_buffer(self):
            return None

        def write(self, value):
            self.writes.append(value)

        def read(self, count):
            if self.delay_next_chunk:
                self.delay_next_chunk = False
                return b""
            if not self.chunks:
                return b""
            chunk = self.chunks[0]
            data = bytes(chunk[:count])
            del chunk[:count]
            if not chunk:
                self.chunks.pop(0)
                if self.chunks:
                    self.delay_next_chunk = True
            return data

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = ChunkedSerial()

    assert radar.read_dump(timeout_s=0.1) == raw
    assert radar.ser.chunks == []
    assert radar.ser.writes == [b"l3dump\n"]


def test_read_dump_reports_firmware_restart_error_after_binary_payload():
    raw = pack_dump(np.ones((1, 3, 4, 4), dtype=complex), n_tx=3, version=3)
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\n" + raw + b"Error: RF restart failed\r\n")

    with pytest.raises(DumpRestartError, match="RF restart failed"):
        radar.read_dump(timeout_s=0.1)


def test_read_dump_sizes_v5_header_extension():
    report = {key: index + 40 for index, key in enumerate(TEMP_REPORT_KEYS)}
    raw = pack_dump(
        np.zeros((2, 4, 4, 8), dtype=complex),
        n_tx=2,
        version=5,
        temperature_report=report,
    )
    serial = FakeSerial(b"cli echo\r\n" + raw + b"trailing cli noise")
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = serial

    dump = radar.read_dump(timeout_s=0.1)

    assert dump == raw
    assert serial.writes == [b"l3dump\n"]


def test_read_dump_drains_stalled_dump_before_next_command():
    raw = pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\n" + raw[: len(raw) // 2])
    drains = []
    radar.drain_stale_output = lambda: drains.append(True)

    partial = radar.read_dump(timeout_s=1.0, stall_tolerance_s=0.05)

    assert len(partial) < len(raw)
    assert drains == [True]


def test_read_dump_does_not_drain_after_complete_dump():
    raw = pack_dump(np.ones((1, 3, 4, 4), dtype=complex), n_tx=3, version=3)
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\n" + raw + b"Done\r\n")
    drains = []
    radar.drain_stale_output = lambda: drains.append(True)

    assert radar.read_dump(timeout_s=0.1) == raw
    assert drains == []


class ScriptedSerial:
    """Serial double that answers each written command with a scripted reply."""

    def __init__(self, replies: dict[bytes, bytes]):
        self.replies = replies
        self.pending = bytearray()
        self.writes: list[bytes] = []

    @property
    def in_waiting(self):
        return len(self.pending)

    def reset_input_buffer(self):
        self.pending.clear()

    def write(self, data: bytes):
        self.writes.append(data)
        self.pending.extend(self.replies[data])

    def read(self, nbytes: int):
        chunk = bytes(self.pending[:nbytes])
        del self.pending[:nbytes]
        return chunk


def _scripted_radar(replies: dict[bytes, bytes]) -> IWR6843Radar:
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = ScriptedSerial(replies)
    return radar


def _summary_reply(scope: int = 0) -> bytes:
    cube = np.ones((3, 6, 4, 8), dtype=complex)
    raw = pack_dump(
        cube,
        n_tx=3,
        version=6,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(4, 4, 4),
        range_bin_counts=(8, 8, 8),
        frame_time_offsets_us=(0, 3000, 6000),
    )
    return encode_summary_reply(summarize_capture(raw), scope)


def test_read_summary_returns_the_binary_reply_past_the_cli_echo():
    reply = _summary_reply(1)
    radar = _scripted_radar({b"l3sum 1\n": b"l3sum 1\r\n" + reply + b"Done\r\nl3dump:/>"})

    assert radar.read_summary(1) == reply
    assert not radar.ser.pending, "the trailing Done is consumed before the next command"


def test_read_windows_reads_exactly_the_requested_length():
    reply = b"ILB1" + bytes(range(40))
    radar = _scripted_radar({b"l3bins 0102\n": b"l3bins 0102\r\n" + reply + b"Done\r\n"})

    assert radar.read_windows("0102", len(reply)) == reply


def test_readback_command_rejected_by_firmware_raises():
    radar = _scripted_radar({b"l3sum 0\n": b"l3sum 0\r\nError: l3freeze first\r\nError -1\r\n"})

    with pytest.raises(RuntimeError, match="l3freeze first"):
        radar.read_summary(0)


def test_stalled_readback_reply_times_out(monkeypatch):
    radar = _scripted_radar({b"l3sum 0\n": b"l3sum 0\r\n" + _summary_reply()[:50]})
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)

    with pytest.raises(TimeoutError, match="l3sum"):
        radar._read_reply("l3sum 0", b"ILS1", lambda _partial: None, timeout_s=0.05)


def test_resume_failure_is_a_restart_error():
    radar = _scripted_radar({b"l3resume\n": b"l3resume\r\nError: restart failed\r\n"})

    with pytest.raises(DumpRestartError):
        radar.resume()


def test_freeze_requires_acknowledgement():
    radar = _scripted_radar({b"l3freeze\n": b"l3freeze\r\nDone\r\n"})

    radar.freeze()

    assert radar.ser.writes == [b"l3freeze\n"]
