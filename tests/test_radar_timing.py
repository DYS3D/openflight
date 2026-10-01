"""Radar timing flags: defaults reproduce the shipped capture cycle exactly.

Each opt-in path (``--fast-clock-sync``, ``--rearm-after-handoff``, the
numeric knobs) is exercised in both states, and the safe-mode fallback is
driven with a fake serial stream that drops the ``]}`` terminator.
"""

import argparse
import json
import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from spin_synth import synth_capture

from openflight.ops243 import OPS243Radar
from openflight.radar_timing import (
    SLOW_DEFAULTS,
    ActiveRadarTiming,
    RadarTimingConfig,
    add_radar_timing_args,
)
from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger


class _ClockSerial:
    """Replies to ``C?`` with a clock payload after an optional delay."""

    def __init__(self, clock_value="137.429", reply_delay_s=0.0):
        self.is_open = True
        self._clock_value = clock_value
        self._reply_delay_s = reply_delay_s
        self._pending = b""
        self._ready_at = 0.0
        self.writes = []

    def reset_input_buffer(self):
        self._pending = b""

    def write(self, data):
        self.writes.append(data)
        self._pending = ('{"Clock":"%s"}' % self._clock_value).encode("ascii")
        self._ready_at = time.monotonic() + self._reply_delay_s
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        if time.monotonic() < self._ready_at:
            return 0
        return len(self._pending)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk


class _StreamSerial:
    """Releases scheduled byte chunks on a wall clock, records writes."""

    def __init__(self, schedule):
        self.is_open = True
        self._schedule = sorted(schedule, key=lambda item: item[0])
        self._t0 = time.time()
        self._consumed = 0
        self.writes = []

    def reset_input_buffer(self):
        self._t0 = time.time()
        self._consumed = 0

    def _released(self):
        elapsed = time.time() - self._t0
        return b"".join(data for t, data in self._schedule if t <= elapsed)

    @property
    def in_waiting(self):
        return len(self._released()) - self._consumed

    def read(self, n):
        released = self._released()
        chunk = released[self._consumed : self._consumed + n]
        self._consumed += len(chunk)
        return chunk

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


def _radar(serial_obj, timing=None) -> OPS243Radar:
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = serial_obj
    radar.last_clock_sync = None
    radar.last_hardware_trigger_first_byte_timestamp = None
    if timing is not None:
        radar.timing = timing
    return radar


FAST = RadarTimingConfig(fast_clock_sync=True, rearm_after_handoff=True)

_COMPLETE_DUMP = (
    b'{"sample_time":946.077}\r\n{"trigger_time":946.145}\r\n'
    b'{"I":[2168,2187,2155,2154]}\r\n'
    b'{"Q":[2048,2050,2047,2049]}'
)
_TRUNCATED_DUMP = _COMPLETE_DUMP[:-3]  # Q array never closes: no "]}"


class TestRadarTimingConfig:
    def test_defaults_are_the_shipped_timing(self):
        config = RadarTimingConfig()
        assert config.clock_sync_samples == 36
        assert config.max_sync_duration_s == 1.25
        assert config.rearm_drain_poll_s == 0.2
        assert config.rearm_after_pa_s == 0.1
        assert config.rearm_after_split_s == 0.1
        assert config.rearm_after_activate_s == 0.15
        assert config.fast_clock_sync is False
        assert config.rearm_after_handoff is False
        assert config == SLOW_DEFAULTS

    def test_config_is_frozen(self):
        with pytest.raises(Exception):
            RadarTimingConfig().fast_clock_sync = True  # type: ignore[misc]

    def test_cli_defaults_round_trip_to_slow_defaults(self):
        parser = argparse.ArgumentParser()
        add_radar_timing_args(parser)
        args = parser.parse_args([])
        assert RadarTimingConfig.from_args(args) == SLOW_DEFAULTS
        assert args.speed_correction_without_angle_radar is False

    def test_cli_flags_set_every_field(self):
        parser = argparse.ArgumentParser()
        add_radar_timing_args(parser)
        args = parser.parse_args(
            [
                "--clock-sync-samples",
                "12",
                "--clock-sync-max-duration",
                "0.5",
                "--rearm-drain-poll",
                "0.05",
                "--rearm-after-pa",
                "0.02",
                "--rearm-after-split",
                "0.03",
                "--rearm-after-activate",
                "0.04",
                "--fast-clock-sync",
                "--rearm-after-handoff",
            ]
        )
        assert RadarTimingConfig.from_args(args) == RadarTimingConfig(
            clock_sync_samples=12,
            max_sync_duration_s=0.5,
            rearm_drain_poll_s=0.05,
            rearm_after_pa_s=0.02,
            rearm_after_split_s=0.03,
            rearm_after_activate_s=0.04,
            fast_clock_sync=True,
            rearm_after_handoff=True,
        )


class TestActiveRadarTiming:
    def test_default_holder_never_enters_safe_mode(self, caplog):
        holder = ActiveRadarTiming()
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            assert holder.enter_safe_mode("truncated dump") is False
        assert holder.safe_mode is False
        assert holder.active == SLOW_DEFAULTS
        assert caplog.records == []

    def test_fast_holder_reverts_once_and_warns_once(self, caplog):
        holder = ActiveRadarTiming(FAST)
        assert holder.active is FAST
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            assert holder.enter_safe_mode("truncated dump") is True
            assert holder.enter_safe_mode("another truncated dump") is False
        assert holder.safe_mode is True
        assert holder.safe_mode_reason == "truncated dump"
        assert holder.active == SLOW_DEFAULTS
        assert holder.active.fast_clock_sync is False
        assert holder.active.rearm_after_handoff is False
        assert holder.requested is FAST
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "truncated dump" in warnings[0].getMessage()

    def test_tuned_numeric_values_also_revert(self):
        holder = ActiveRadarTiming(RadarTimingConfig(clock_sync_samples=4, rearm_after_pa_s=0.01))
        assert holder.enter_safe_mode("truncated dump") is True
        assert holder.active.clock_sync_samples == 36
        assert holder.active.rearm_after_pa_s == 0.1


class TestFastClockSync:
    def test_default_takes_every_sample(self):
        radar = _radar(_ClockSerial("137.429"), ActiveRadarTiming())
        summary = radar.read_clock_sync(samples=5, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 5
        assert summary["valid_samples"] == 5
        assert "fast_clock_sync_early_exit" not in summary

    def test_fast_flag_stops_at_first_precise_fractional_reply(self):
        radar = _radar(_ClockSerial("137.429"), ActiveRadarTiming(FAST))
        summary = radar.read_clock_sync(samples=5, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 1
        assert summary["valid_samples"] == 1
        assert summary["fast_clock_sync_early_exit"] is True
        assert summary["clock_sync_method"] == "fractional_clock"
        assert summary["usable_for_trigger_timestamps"] is True
        assert summary["best_read_latency_ms"] < OPS243Radar.FAST_CLOCK_SYNC_MAX_LATENCY_MS

    def test_fast_flag_keeps_sampling_when_reply_is_slow(self):
        # A 6 ms bracket is above the 3 ms cap: no early exit.
        radar = _radar(_ClockSerial("137.429", reply_delay_s=0.006), ActiveRadarTiming(FAST))
        summary = radar.read_clock_sync(samples=3, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 3
        assert "fast_clock_sync_early_exit" not in summary

    def test_fast_flag_keeps_sampling_for_integer_clock(self):
        radar = _radar(_ClockSerial("1882"), ActiveRadarTiming(FAST))
        summary = radar.read_clock_sync(
            samples=3, per_read_timeout=0.05, sample_interval_s=0.0, max_sync_duration_s=0.0
        )
        assert summary["samples"] == 3
        assert summary["clock_resolution"] == "integer"
        assert "fast_clock_sync_early_exit" not in summary

    def test_explicit_argument_overrides_holder(self):
        radar = _radar(_ClockSerial("137.429"), ActiveRadarTiming(FAST))
        summary = radar.read_clock_sync(
            samples=4, per_read_timeout=0.05, sample_interval_s=0.0, fast_clock_sync=False
        )
        assert summary["samples"] == 4

    def test_instance_without_holder_uses_slow_defaults(self):
        radar = _radar(_ClockSerial("137.429"))
        assert radar.timing is None
        summary = radar.read_clock_sync(samples=3, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 3

    def test_max_sync_duration_defaults_from_holder(self):
        # An integer clock with no rollover keeps sampling until the cap;
        # a zero cap from the holder returns after the requested samples.
        radar = _radar(
            _ClockSerial("1882"), ActiveRadarTiming(RadarTimingConfig(max_sync_duration_s=0.0))
        )
        start = time.monotonic()
        summary = radar.read_clock_sync(samples=2, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 2
        assert summary["clock_sync_method"] == "integer_unusable_no_rollover"
        assert time.monotonic() - start < 0.5

    def test_safe_mode_switches_fast_sync_off(self):
        holder = ActiveRadarTiming(FAST)
        holder.enter_safe_mode("truncated dump")
        radar = _radar(_ClockSerial("137.429"), holder)
        summary = radar.read_clock_sync(samples=3, per_read_timeout=0.05, sample_interval_s=0.0)
        assert summary["samples"] == 3


class TestRearmSleeps:
    def _rearm_sleeps(self, timing):
        radar = _radar(_StreamSerial([]), timing)
        sleeps = []
        with patch("openflight.ops243.time.sleep", side_effect=sleeps.append):
            radar.rearm_rolling_buffer(pre_trigger_segments=16)
        assert radar.serial.writes == [b"PA", b"S#16\r", b"PA"]
        return sleeps

    def test_default_sleeps_are_unchanged(self):
        assert self._rearm_sleeps(ActiveRadarTiming()) == [0.2, 0.1, 0.1, 0.15]

    def test_instance_without_holder_uses_default_sleeps(self):
        assert self._rearm_sleeps(None) == [0.2, 0.1, 0.1, 0.15]

    def test_configured_sleeps_are_used_in_order(self):
        timing = ActiveRadarTiming(
            RadarTimingConfig(
                rearm_drain_poll_s=0.01,
                rearm_after_pa_s=0.02,
                rearm_after_split_s=0.03,
                rearm_after_activate_s=0.04,
            )
        )
        assert self._rearm_sleeps(timing) == [0.01, 0.02, 0.03, 0.04]

    def test_safe_mode_restores_default_sleeps(self):
        timing = ActiveRadarTiming(RadarTimingConfig(rearm_drain_poll_s=0.01))
        timing.enter_safe_mode("truncated dump")
        assert self._rearm_sleeps(timing) == [0.2, 0.1, 0.1, 0.15]


class TestSafeModeFallback:
    def test_truncated_hardware_dump_reverts_fast_timing(self, caplog):
        timing = ActiveRadarTiming(FAST)
        radar = _radar(_StreamSerial([(0.02, _TRUNCATED_DUMP)]), timing)
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            response = radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=1.0)
        assert response == _TRUNCATED_DUMP.decode("ascii")
        assert timing.safe_mode is True
        assert timing.active == SLOW_DEFAULTS
        assert "Hardware trigger" in timing.safe_mode_reason
        assert "]}" in timing.safe_mode_reason
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_complete_hardware_dump_keeps_fast_timing(self, caplog):
        timing = ActiveRadarTiming(FAST)
        radar = _radar(_StreamSerial([(0.02, _COMPLETE_DUMP)]), timing)
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            response = radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=1.0)
        assert response == _COMPLETE_DUMP.decode("ascii")
        assert timing.safe_mode is False
        assert timing.active is FAST
        assert caplog.records == []

    def test_idle_timeout_without_a_dump_keeps_fast_timing(self):
        timing = ActiveRadarTiming(FAST)
        radar = _radar(_StreamSerial([]), timing)
        assert radar.wait_for_hardware_trigger(timeout=0.1, dump_grace=0.1) == ""
        assert timing.safe_mode is False

    def test_truncated_dump_on_default_timing_stays_silent(self, caplog):
        timing = ActiveRadarTiming()
        radar = _radar(_StreamSerial([(0.02, _TRUNCATED_DUMP)]), timing)
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=1.0)
        assert timing.safe_mode is False
        assert caplog.records == []

    def test_second_truncated_dump_does_not_warn_again(self, caplog):
        timing = ActiveRadarTiming(FAST)
        with caplog.at_level(logging.WARNING, logger="openflight.radar_timing"):
            for _ in range(2):
                radar = _radar(_StreamSerial([(0.02, _TRUNCATED_DUMP)]), timing)
                radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=1.0)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_truncated_software_trigger_dump_reverts_fast_timing(self):
        timing = ActiveRadarTiming(FAST)
        radar = _radar(_StreamSerial([(0.02, _TRUNCATED_DUMP)]), timing)
        with patch.object(OPS243Radar, "transfer_budget_s", return_value=1.0):
            response = radar.trigger_capture(timeout=1.0)
        assert response == _TRUNCATED_DUMP.decode("ascii")
        assert radar.serial.writes == [b"S!\r"]
        assert timing.safe_mode is True
        assert "S! trigger" in timing.safe_mode_reason

    def test_instance_without_holder_ignores_truncation(self):
        radar = _radar(_StreamSerial([(0.02, _TRUNCATED_DUMP)]))
        response = radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=1.0)
        assert response == _TRUNCATED_DUMP.decode("ascii")


def _dump_response(i_samples, q_samples) -> str:
    return "\n".join(
        [
            '{"sample_time": "964.003"}',
            '{"trigger_time": "964.105"}',
            json.dumps({"I": [int(v) for v in i_samples]}),
            json.dumps({"Q": [int(v) for v in q_samples]}),
        ]
    )


class _ScriptedRadar:
    """Records the order of serial-touching calls the trigger makes."""

    def __init__(self, response: str):
        self.response = response
        self.calls = []
        self.clock_sync_samples = []
        self.last_clock_sync = None
        self.last_hardware_trigger_first_byte_timestamp = None

    def wait_for_hardware_trigger(self, timeout, cancel_event=None, on_first_byte=None):
        self.calls.append("wait")
        return self.response

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.calls.append("rearm")

    def read_clock_sync(self, samples=7, store=True, **kwargs):
        self.calls.append("clock_sync")
        self.clock_sync_samples.append(samples)
        return {"clock_sync_method": "no_valid_reads", "valid_samples": 0}


def _swing_response() -> str:
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    return _dump_response(i_samples, q_samples)


def _silent_response() -> str:
    i_samples, q_samples = synth_capture(rpm=3000, amplitude=0.0, noise_rms=1.0)
    return _dump_response(i_samples, q_samples)


class TestSoundTriggerTiming:
    def test_default_uses_36_clock_sync_samples(self):
        radar = _ScriptedRadar(_swing_response())
        trigger = SoundTrigger()
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is not None
        assert radar.clock_sync_samples == [36]

    def test_configured_clock_sync_samples_reach_the_radar(self):
        radar = _ScriptedRadar(_swing_response())
        trigger = SoundTrigger(timing=ActiveRadarTiming(RadarTimingConfig(clock_sync_samples=8)))
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is not None
        assert radar.clock_sync_samples == [8]

    def test_default_rearms_inside_wait_for_trigger(self):
        radar = _ScriptedRadar(_swing_response())
        trigger = SoundTrigger()
        capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
        assert capture is not None
        assert radar.calls == ["wait", "clock_sync", "rearm"]
        assert trigger.finish_deferred_rearm(radar) is False
        assert radar.calls == ["wait", "clock_sync", "rearm"]

    def test_rearm_after_handoff_defers_the_rearm(self):
        radar = _ScriptedRadar(_swing_response())
        trigger = SoundTrigger(timing=ActiveRadarTiming(FAST))
        capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
        assert capture is not None
        assert radar.calls == ["wait", "clock_sync"], "re-arm must wait for the hand-off"
        assert trigger.finish_deferred_rearm(radar) is True
        assert radar.calls == ["wait", "clock_sync", "rearm"]
        assert trigger.finish_deferred_rearm(radar) is False
        assert radar.calls == ["wait", "clock_sync", "rearm"]

    def test_rearm_after_handoff_still_rearms_rejected_captures_at_once(self):
        radar = _ScriptedRadar(_silent_response())
        trigger = SoundTrigger(timing=ActiveRadarTiming(FAST))
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is None
        assert radar.calls == ["wait", "rearm"]
        assert trigger.finish_deferred_rearm(radar) is False

    def test_safe_mode_restores_immediate_rearm(self):
        timing = ActiveRadarTiming(FAST)
        timing.enter_safe_mode("truncated dump")
        radar = _ScriptedRadar(_swing_response())
        trigger = SoundTrigger(timing=timing)
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is not None
        assert radar.calls == ["wait", "clock_sync", "rearm"]
        assert radar.clock_sync_samples == [36]

    def test_swallowed_dump_rearms_the_idle_radar(self):
        radar = _ScriptedRadar("")
        radar.last_hardware_trigger_swallowed_dump = True
        trigger = SoundTrigger()
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is None
        assert radar.calls == ["wait", "rearm"]

    def test_plain_timeout_does_not_rearm(self):
        radar = _ScriptedRadar("")
        radar.last_hardware_trigger_swallowed_dump = False
        trigger = SoundTrigger()
        assert trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0) is None
        assert radar.calls == ["wait"]


class TestMonitorTimingPlumbing:
    def test_monitor_shares_one_holder_with_radar_and_trigger(self):
        config = RadarTimingConfig(clock_sync_samples=8, rearm_after_handoff=True)
        monitor = RollingBufferMonitor(port=None, trigger_type="sound", radar_timing=config)
        assert monitor.timing.requested is config
        assert monitor.radar.timing is monitor.timing
        assert monitor.trigger.timing is monitor.timing

    def test_monitor_defaults_to_slow_timing(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        assert monitor.timing.active == SLOW_DEFAULTS
        assert monitor.radar.timing is monitor.timing
        assert monitor.trigger.timing is monitor.timing

    def test_speed_trigger_does_not_receive_the_holder(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="speed")
        assert monitor.radar.timing is monitor.timing
        assert not hasattr(monitor.trigger, "timing")

    @pytest.mark.parametrize("rearm_after_handoff", [False, True])
    def test_capture_loop_orders_shot_callback_and_rearm(self, monkeypatch, rearm_after_handoff):
        """Default: re-arm precedes the shot callback. Flag: callback first."""
        from openflight.rolling_buffer import monitor as monitor_module

        config = RadarTimingConfig(rearm_after_handoff=rearm_after_handoff)
        monitor = RollingBufferMonitor(port=None, trigger_type="sound", radar_timing=config)
        events = []
        radar = _ScriptedRadar(_swing_response())
        radar.calls = events
        monitor.radar = radar

        class OneShotTrigger(SoundTrigger):
            calls = 0

            def wait_for_trigger(self, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    monitor._running = False
                    return None
                return super().wait_for_trigger(
                    kwargs["radar"], kwargs["processor"], timeout=kwargs["timeout"]
                )

        monitor.trigger = OneShotTrigger(timing=monitor.timing)
        monitor._shot_callback = lambda shot: events.append("callback")
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
        monitor._running = True

        monitor._capture_loop()

        assert events.count("rearm") == 1
        assert events.count("callback") == 1
        if rearm_after_handoff:
            assert events.index("callback") < events.index("rearm")
        else:
            assert events.index("rearm") < events.index("callback")

    def test_deferred_rearm_runs_when_processing_fails(self, monkeypatch):
        from openflight.rolling_buffer import monitor as monitor_module

        config = RadarTimingConfig(rearm_after_handoff=True)
        monitor = RollingBufferMonitor(port=None, trigger_type="sound", radar_timing=config)
        radar = _ScriptedRadar(_swing_response())
        monitor.radar = radar

        class OneShotTrigger(SoundTrigger):
            calls = 0

            def wait_for_trigger(self, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    monitor._running = False
                    return None
                return super().wait_for_trigger(
                    kwargs["radar"], kwargs["processor"], timeout=kwargs["timeout"]
                )

        class FailingProcessor(RollingBufferProcessor):
            def process_capture(self, *_args, **_kwargs):
                return None

        monitor.trigger = OneShotTrigger(timing=monitor.timing)
        monitor.processor = FailingProcessor()
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
        monitor._running = True

        monitor._capture_loop()

        assert radar.calls == ["wait", "clock_sync", "rearm"]

    def test_deferred_rearm_runs_when_processing_raises(self, monkeypatch):
        from openflight.rolling_buffer import monitor as monitor_module

        config = RadarTimingConfig(rearm_after_handoff=True)
        monitor = RollingBufferMonitor(port=None, trigger_type="sound", radar_timing=config)
        radar = _ScriptedRadar(_swing_response())
        monitor.radar = radar

        class OneShotTrigger(SoundTrigger):
            calls = 0

            def wait_for_trigger(self, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    monitor._running = False
                    return None
                return super().wait_for_trigger(
                    kwargs["radar"], kwargs["processor"], timeout=kwargs["timeout"]
                )

        class ExplodingProcessor(RollingBufferProcessor):
            def process_capture(self, *_args, **_kwargs):
                raise RuntimeError("FFT failed")

        monitor.trigger = OneShotTrigger(timing=monitor.timing)
        monitor.processor = ExplodingProcessor()
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(monitor_module.time, "sleep", lambda _delay: None)
        monitor._running = True

        monitor._capture_loop()

        assert radar.calls == ["wait", "clock_sync", "rearm"]

    def test_capture_loop_tolerates_triggers_without_deferred_rearm(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.trigger = MagicMock(spec=[])
        monitor._finish_deferred_rearm()

    def test_deferred_rearm_failure_is_logged_not_raised(self, caplog):
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        trigger = MagicMock()
        trigger.finish_deferred_rearm.side_effect = ConnectionError("port closed")
        monitor.trigger = trigger
        with caplog.at_level(logging.WARNING, logger="openflight.rolling_buffer.monitor"):
            monitor._finish_deferred_rearm()
        assert any("Deferred re-arm failed" in r.getMessage() for r in caplog.records)
