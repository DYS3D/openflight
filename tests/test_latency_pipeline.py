"""Shot-to-screen latency work: instrumentation, radar profile, fast DSP, gating.

Every feature here ships off by default. The default-state tests pin today's
behaviour (kwargs, payload shape, processing order) so the flags cannot leak.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.radar_profile import (
    BUFFER_SEGMENTS,
    LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS,
    LOW_LATENCY_SAMPLE_RATE_KSPS,
    RadarProfileSettings,
    resolve_radar_profile,
)
from openflight.rolling_buffer import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import (
    IQCapture,
    ProcessedCapture,
    SpeedTimeline,
    SpinResult,
)
from openflight.server import on_shot_detected

SESSION_LOG = Path(__file__).parent.parent / "session_logs" / "session_20260501_180406_range.jsonl"


def _committed_captures() -> list[IQCapture]:
    captures = []
    with SESSION_LOG.open() as handle:
        for line in handle:
            entry = json.loads(line)
            if entry["type"] == "rolling_buffer_capture":
                captures.append(
                    IQCapture(
                        sample_time=entry["sample_time"],
                        trigger_time=entry["trigger_time"],
                        i_samples=entry["i_samples"],
                        q_samples=entry["q_samples"],
                    )
                )
    assert len(captures) == 9
    return captures


def _synthetic_capture(speed_mph: float, sample_rate_hz: float) -> IQCapture:
    """A clean outbound Doppler tone at ``speed_mph`` sampled at ``sample_rate_hz``."""
    processor = RollingBufferProcessor
    doppler_hz = (speed_mph / processor.MPS_TO_MPH) / (processor.WAVELENGTH_M / 2)
    t = np.arange(4096) / sample_rate_hz
    phase = 2 * np.pi * doppler_hz * t
    amplitude = 500.0 * (1.0 + 0.05 * np.sin(2 * np.pi * 150.0 * t))
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=(2048 + amplitude * np.cos(phase)).astype(int).tolist(),
        q_samples=(2048 + amplitude * np.sin(phase)).astype(int).tolist(),
        sample_rate_hz=sample_rate_hz,
    )


def _wait_for_shot_finalization_idle(timeout_s: float = 3.0) -> None:
    with server_module._shot_finalization_condition:
        idle = server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=timeout_s,
        )
    assert idle, "shot finalization coordinator did not become idle"


class FakeClock:
    """Deterministic ``time.time`` that advances a fixed step per call."""

    def __init__(self, start: float, step: float = 0.010):
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


# =============================================================================
# 1. Latency instrumentation
# =============================================================================


class TestShotLatencyMarks:
    def test_latency_ms_is_relative_to_impact_and_rounded(self):
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=1000.0)
        shot.mark_stage("capture", 1000.1234)
        shot.mark_stage("final", 1000.9)

        assert shot.latency_ms() == {"capture": 123.4, "final": 900.0}

    def test_latency_ms_without_trigger_timestamp_is_none_per_stage(self):
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
        shot.mark_stage("final", 5.0)

        assert shot.latency_ms() == {"final": None}

    def test_marks_are_not_part_of_the_logged_shot_dict(self):
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=1.0)
        shot.mark_stage("final", 2.0)

        data = shot.to_dict()
        assert "pipeline_marks" not in data
        assert "latency_ms" not in data
        assert "iwr6843_status" not in data
        assert "camera_status" not in data

    def test_monitor_marks_capture_and_processing_time(self, monkeypatch):
        clock = FakeClock(12346.0)
        monkeypatch.setattr("openflight.rolling_buffer.monitor.time.time", clock)
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
            first_byte_timestamp=12345.746,
            trigger_timestamp=12345.678,
            timestamp=12345.900,
        )
        processed = ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
            ball_speed_mph=100.0,
            ball_timestamp_ms=60.0,
            club_speed_mph=75.0,
            spin=SpinResult(spin_rpm=0, confidence=0.0, snr=0.0, quality="none"),
            capture=capture,
        )
        monitor = RollingBufferMonitor(port=None, trigger_type="sound")

        shot = monitor._create_shot(processed)

        assert shot.pipeline_marks["capture"] == 12345.900
        assert shot.pipeline_marks["processed"] == pytest.approx(12346.01)
        assert shot.latency_ms()["capture"] == pytest.approx(222.0)


class TestServerLatencyInstrumentation:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        server_module._reset_shot_sequence()
        self.emitted = []
        self.logged = []
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
        monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "sim_connectors", [])
        monkeypatch.setattr(server_module, "gated_postprocessing", False)
        monkeypatch.setattr(
            server_module,
            "get_session_logger",
            lambda: SimpleNamespace(
                log_shot=lambda shot, pipeline_ms=None: self.logged.append(pipeline_ms),
                log_iwr6843_capture=lambda **_kwargs: None,
                log_camera_capture=lambda **_kwargs: None,
            ),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: self.emitted.append((event, payload)),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *args, **kwargs: threading.Thread(
                target=target, args=args, kwargs=kwargs, daemon=True
            ).start(),
        )
        yield
        _wait_for_shot_finalization_idle()

    @staticmethod
    def _shot() -> Shot:
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime(2026, 9, 30, 12, 0, 0),
            impact_timestamp=1000.0,
            club=ClubType.DRIVER,
            mode="rolling-buffer",
        )
        shot.mark_stage("capture", 1000.25)
        shot.mark_stage("processed", 1000.30)
        return shot

    def test_ops_only_shot_reports_each_stage_and_logs_one_latency_line(self, monkeypatch, caplog):
        clock = FakeClock(1000.30, step=0.100)
        monkeypatch.setattr(server_module.time, "time", clock)

        with caplog.at_level(logging.INFO, logger="openflight.server"):
            on_shot_detected(self._shot())
            _wait_for_shot_finalization_idle()

        events = [event for event, _payload in self.emitted]
        assert events == ["shot"]
        latency = self.emitted[0][1]["shot"]["latency_ms"]
        assert latency["capture"] == 250.0
        assert latency["processed"] == 300.0
        assert "initial_ui" not in latency
        # Every stage stamp comes from the fake clock, so they are exact
        # multiples of its step and strictly ordered.
        assert latency["processed"] < latency["carry"] < latency["ready"]
        assert latency["carry"] % 100.0 == 0.0
        assert latency["ready"] == latency["carry"] + 100.0
        assert self.logged[0]["latency"] == latency
        assert self.logged[0]["initial_ui"] is None
        latency_lines = [r.getMessage() for r in caplog.records if "[LATENCY]" in r.getMessage()]
        assert latency_lines == [
            "[LATENCY] shot #1: trigger→ui %.0f ms, →ready %.0f ms "
            "(→capture 250 ms, →processed 300 ms, →carry %.0f ms)"
            % (latency["ready"], latency["ready"], latency["carry"])
        ]

    def test_enriched_shot_marks_initial_ui_and_iwr6843(self, monkeypatch, caplog):
        clock = FakeClock(1000.30, step=0.100)
        monkeypatch.setattr(server_module.time, "time", clock)
        monkeypatch.setattr(
            server_module,
            "iwr6843_runtime",
            SimpleNamespace(
                process_shot=lambda **_kwargs: SimpleNamespace(
                    capture=None, measurement=None, club_path=None
                )
            ),
        )

        with caplog.at_level(logging.INFO, logger="openflight.server"):
            on_shot_detected(self._shot())
            _wait_for_shot_finalization_idle()

        events = [event for event, _payload in self.emitted]
        assert events[0] == "shot"
        assert events[-1] == "shot_update"
        assert "latency_ms" not in self.emitted[0][1]["shot"]
        latency = self.emitted[-1][1]["shot"]["latency_ms"]
        assert latency["initial_ui"] % 100.0 == 0.0
        assert latency["processed"] < latency["initial_ui"] < latency["iwr6843"]
        assert latency["iwr6843"] < latency["carry"] < latency["ready"]
        assert self.logged[0]["initial_ui"] == latency["initial_ui"]
        assert self.logged[0]["latency"] == latency
        line = next(r.getMessage() for r in caplog.records if "[LATENCY]" in r.getMessage())
        assert line.startswith(
            "[LATENCY] shot #1: trigger→ui %.0f ms, →ready %.0f ms (→capture 250 ms, "
            "→processed 300 ms, →iwr6843 %.0f ms, →carry %.0f ms)"
            % (latency["initial_ui"], latency["ready"], latency["iwr6843"], latency["carry"])
        )

    def test_shot_without_trigger_timestamp_logs_no_latency_numbers(self, caplog):
        shot = self._shot()
        shot.impact_timestamp = None

        with caplog.at_level(logging.INFO, logger="openflight.server"):
            on_shot_detected(shot)
            _wait_for_shot_finalization_idle()

        latency = self.emitted[0][1]["shot"]["latency_ms"]
        assert set(latency) == {"capture", "processed", "carry", "ready"}
        assert all(value is None for value in latency.values())
        assert any(
            "[LATENCY] shot #1: no trigger timestamp" in r.getMessage() for r in caplog.records
        )


# =============================================================================
# 2. --radar-profile
# =============================================================================


class TestRadarProfile:
    def test_standard_passes_flags_through_untouched(self):
        settings = resolve_radar_profile("standard", sample_rate_ksps=30, pre_trigger_segments=16)

        assert settings == RadarProfileSettings("standard", 30, 16, False)
        assert settings.segment_ms == pytest.approx(128 / 30)
        assert settings.pre_trigger_ms == pytest.approx(68.27, abs=0.01)
        assert settings.post_trigger_ms == pytest.approx(68.27, abs=0.01)
        assert settings.buffer_ms == pytest.approx(136.53, abs=0.01)

    def test_low_latency_caps_pre_trigger_to_leave_post_impact_signal(self):
        settings = resolve_radar_profile(
            "low-latency", sample_rate_ksps=30, pre_trigger_segments=16
        )

        assert settings.sample_rate_ksps == LOW_LATENCY_SAMPLE_RATE_KSPS == 50
        assert settings.scale_speed_band is True
        # 16 x 4.27 ms = 68.3 ms would need 27 segments of 2.56 ms (12.8 ms post).
        assert settings.pre_trigger_segments == LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS == 20
        assert settings.pre_trigger_ms == pytest.approx(51.2)
        assert settings.post_trigger_ms == pytest.approx(30.72)
        assert settings.buffer_ms == pytest.approx(81.92)

    def test_low_latency_post_trigger_fits_the_spin_minimum(self):
        settings = resolve_radar_profile(
            "low-latency", sample_rate_ksps=30, pre_trigger_segments=16
        )
        processor = RollingBufferProcessor(
            sample_rate=settings.sample_rate_ksps * 1000,
            scale_speed_band=settings.scale_speed_band,
        )

        post_samples = (BUFFER_SEGMENTS - settings.pre_trigger_segments) * 128
        assert post_samples // processor.WINDOW_SIZE == 12
        assert post_samples > processor.SPIN_MIN_SAMPLES
        assert processor.SPIN_MIN_SAMPLES / processor.SAMPLE_RATE == pytest.approx(0.020)

    @pytest.mark.parametrize("pre_trigger", [0, 12, 32])
    def test_low_latency_split_stays_inside_the_buffer(self, pre_trigger):
        settings = resolve_radar_profile(
            "low-latency", sample_rate_ksps=30, pre_trigger_segments=pre_trigger
        )

        assert 0 <= settings.pre_trigger_segments <= LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS
        expected_ms = min(pre_trigger * 128 / 30, LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS * 2.56)
        assert settings.pre_trigger_ms == pytest.approx(expected_ms, abs=settings.segment_ms / 2)

    def test_unknown_profile_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_radar_profile("turbo", sample_rate_ksps=30, pre_trigger_segments=16)


class TestRadarProfileWiring:
    @pytest.fixture
    def fake_monitor(self, monkeypatch):
        created = {}

        class FakeRollingBufferMonitor:
            def __init__(self, **kwargs):
                created.update(kwargs)
                self.radar = SimpleNamespace(baud=57600)

            def connect(self):
                return True

            def get_radar_info(self):
                return {}

            def start(self, **_kwargs):
                pass

            def stop(self):
                pass

            def disconnect(self):
                pass

        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            "openflight.rolling_buffer.RollingBufferMonitor", FakeRollingBufferMonitor
        )
        yield created
        server_module.stop_monitor()

    def test_default_profile_constructs_today_monitor(self, fake_monitor):
        server_module.start_monitor(
            port="/dev/ops",
            trigger_kwargs={"pre_trigger_segments": 16, "radar_timing": None},
            sample_rate_ksps=30,
        )

        assert fake_monitor == {
            "port": "/dev/ops",
            "trigger_type": "sound",
            "sample_rate_ksps": 30,
            "ops_baud": None,
            "radar_auto_reconnect": False,
            "ball_marker": "none",
            "spin_octave_check": False,
            "spin_octave_prior": "optimal",
            "cap_spin_prior": False,
            "ball_speed_magnitude_gate": False,
            "spin_harmonic_fit": False,
            "interference_check": False,
            "scale_speed_band": False,
            "fast_dsp": False,
            "runtime_rolling_buffer": False,
            "pre_trigger_segments": 16,
            "radar_timing": None,
        }

    def test_default_profile_without_trigger_kwargs_leaves_trigger_defaults(self, fake_monitor):
        server_module.start_monitor(port="/dev/ops")

        assert "pre_trigger_segments" not in fake_monitor
        assert fake_monitor["sample_rate_ksps"] == 30

    def test_low_latency_profile_overrides_rate_and_split(self, fake_monitor):
        server_module.start_monitor(
            port="/dev/ops",
            trigger_kwargs={"pre_trigger_segments": 16, "radar_timing": None},
            sample_rate_ksps=30,
            radar_profile="low-latency",
            fast_dsp=True,
        )

        assert fake_monitor["sample_rate_ksps"] == 50
        assert fake_monitor["pre_trigger_segments"] == 20
        assert fake_monitor["scale_speed_band"] is True
        assert fake_monitor["fast_dsp"] is True

    def test_cli_defaults_are_standard_and_off(self, monkeypatch):
        from tests.test_server import _run_main_until_start_monitor

        captured = _run_main_until_start_monitor(monkeypatch, [])
        assert captured["radar_profile"] == "standard"
        assert captured["fast_dsp"] is False

    def test_cli_flags_reach_start_monitor(self, monkeypatch):
        from tests.test_server import _run_main_until_start_monitor

        captured = _run_main_until_start_monitor(
            monkeypatch, ["--radar-profile", "low-latency", "--fast-dsp"]
        )
        assert captured["radar_profile"] == "low-latency"
        assert captured["fast_dsp"] is True


class TestProcessorSampleRateHandling:
    def test_defaults_keep_30_ksps_bin_constants(self):
        processor = RollingBufferProcessor()
        assert processor.SAMPLE_RATE == 30000
        assert processor.DC_MASK_BINS == 150
        assert processor.MIN_PEAK_SEPARATION_BINS == 50
        assert processor.fast_dsp is False

    def test_scale_speed_band_keeps_mph_meaning_at_50_ksps(self):
        processor = RollingBufferProcessor(sample_rate=50000, scale_speed_band=True)
        assert processor.DC_MASK_BINS == 90
        assert processor.MIN_PEAK_SEPARATION_BINS == 30
        # Unscaled 50 ksps keeps the historical bin counts.
        assert RollingBufferProcessor(sample_rate=50000).DC_MASK_BINS == 150

    def test_spin_sample_counts_unchanged_at_30_ksps(self):
        for processor in (
            RollingBufferProcessor(),
            RollingBufferProcessor(sample_rate=30000, scale_speed_band=True),
            RollingBufferProcessor(sample_rate=50000),
        ):
            assert processor.SPIN_MIN_SAMPLES == 600
            assert processor.SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES == 90
            assert processor.SPIN_SIGNAL_LOSS_REF_SAMPLES == 450
            assert processor.SPIN_SIGNAL_LOSS_HOLD_SAMPLES == 150
            assert processor.SPIN_SIGNAL_LOSS_THRESHOLD == 0.15
            assert processor.MULTITAPER_MIN_SAMPLES == 128

    def test_scale_speed_band_keeps_spin_durations_at_50_ksps(self):
        processor = RollingBufferProcessor(sample_rate=50000, scale_speed_band=True)
        assert processor.SPIN_MIN_SAMPLES == 1000
        assert processor.SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES == 150
        assert processor.SPIN_SIGNAL_LOSS_REF_SAMPLES == 750
        assert processor.SPIN_SIGNAL_LOSS_HOLD_SAMPLES == 250
        assert processor.SPIN_SIGNAL_LOSS_THRESHOLD == 0.15
        assert processor.MULTITAPER_MIN_SAMPLES == 213
        # Class constants are untouched for other processors.
        assert RollingBufferProcessor.SPIN_MIN_SAMPLES == 600

    def test_capture_duration_follows_its_sample_rate(self):
        default = IQCapture(0.0, 0.068, [2048] * 4096, [2048] * 4096)
        fast = IQCapture(0.0, 0.041, [2048] * 4096, [2048] * 4096, sample_rate_hz=50000.0)

        assert default.duration_ms == pytest.approx(136.53, abs=0.01)
        assert fast.duration_ms == pytest.approx(81.92)
        assert fast.post_trigger_duration_ms == pytest.approx(40.92)

    def test_parse_capture_stamps_the_processor_rate(self):
        response = "\n".join(
            [
                '{"sample_time": "1.000"}',
                '{"trigger_time": "1.041"}',
                json.dumps({"I": [2048] * 4096}),
                json.dumps({"Q": [2048] * 4096}),
            ]
        )
        capture = RollingBufferProcessor(sample_rate=50000).parse_capture(response)
        assert capture.sample_rate_hz == 50000.0
        assert RollingBufferProcessor().parse_capture(response).sample_rate_hz == 30000.0

    def test_synthetic_50_ksps_tone_reads_the_right_speed(self):
        capture = _synthetic_capture(100.0, 50000.0)

        fast = RollingBufferProcessor(sample_rate=50000, scale_speed_band=True)
        result = fast.process_capture(capture)
        assert result is not None
        assert result.ball_speed_mph == pytest.approx(100.0, abs=0.5)

        # A processor that still assumed 30 ksps would misread the same samples.
        wrong = RollingBufferProcessor().process_capture(capture)
        assert wrong is not None
        assert wrong.ball_speed_mph == pytest.approx(60.0, abs=0.5)

    def test_synthetic_30_ksps_tone_is_unchanged_by_the_new_options(self):
        capture = _synthetic_capture(100.0, 30000.0)
        baseline = RollingBufferProcessor().process_capture(capture)
        scaled = RollingBufferProcessor(sample_rate=30000, scale_speed_band=True).process_capture(
            capture
        )
        assert baseline.ball_speed_mph == scaled.ball_speed_mph == pytest.approx(100.0, abs=0.5)


# =============================================================================
# 3. --fast-dsp
# =============================================================================


@pytest.fixture(scope="module")
def captures():
    return _committed_captures()


class TestFastDsp:
    @staticmethod
    def _readings(timeline):
        return [(r.speed_mph, r.magnitude, r.timestamp_ms, r.direction) for r in timeline.readings]

    def test_committed_captures_process_identically(self, captures):
        default = RollingBufferProcessor()
        fast = RollingBufferProcessor(fast_dsp=True)
        for capture in captures:
            expected = default.process_capture(capture)
            actual = fast.process_capture(capture)
            assert actual is not None and expected is not None
            assert actual.ball_speed_mph == pytest.approx(expected.ball_speed_mph, abs=1e-9)
            assert actual.club_speed_mph == pytest.approx(expected.club_speed_mph, abs=1e-9)
            assert actual.ball_timestamp_ms == expected.ball_timestamp_ms
            assert actual.spin.spin_rpm == pytest.approx(expected.spin.spin_rpm, abs=1e-9)
            assert actual.spin.snr == pytest.approx(expected.spin.snr, abs=1e-9)
            expected_readings = self._readings(expected.timeline)
            actual_readings = self._readings(actual.timeline)
            assert len(actual_readings) == len(expected_readings)
            for got, want in zip(actual_readings, expected_readings):
                assert got[3] == want[3]
                assert np.allclose(got[:3], want[:3], atol=1e-9, rtol=0)

    def test_standard_timeline_matches_too(self, captures):
        default = RollingBufferProcessor()
        fast = RollingBufferProcessor(fast_dsp=True)
        for capture in captures:
            expected = self._readings(default.process_standard(capture))
            actual = self._readings(fast.process_standard(capture))
            assert len(actual) == len(expected)
            for got, want in zip(actual, expected):
                assert got[3] == want[3]
                assert np.allclose(got[:3], want[:3], atol=1e-9, rtol=0)

    def test_batched_peak_picker_matches_per_window_picker_including_ties(self):
        rng = np.random.default_rng(7)
        for dc_mask in (150, 90, 2047, 2100):
            processor = RollingBufferProcessor(fast_dsp=True)
            processor.DC_MASK_BINS = dc_mask
            magnitudes = rng.integers(0, 12, size=(40, processor.FFT_SIZE)).astype(np.float64)
            batched = processor._window_peaks_fast(magnitudes)
            assert batched == [processor._peaks_from_magnitude(row) for row in magnitudes]

    def test_fast_path_reuses_the_trigger_standard_timeline(self, captures):
        fast = RollingBufferProcessor(fast_dsp=True)
        capture = captures[0]
        capture.standard_timeline = fast.process_standard(capture)
        standard, overlapping = fast._process_overlapping_with_standard(capture)
        assert standard is capture.standard_timeline
        assert len(overlapping.readings) > len(standard.readings)


# =============================================================================
# 4. --gated-postprocessing
# =============================================================================


class _SlowIwrRuntime:
    """IWR6843 stand-in whose dump takes ``delay_s`` before reporting an angle."""

    def __init__(self, delay_s: float, horizontal_deg: float | None = None):
        self.delay_s = delay_s
        self.horizontal_deg = horizontal_deg
        self.calls = 0
        self.finished = threading.Event()

    def process_shot(self, **_kwargs):
        self.calls += 1
        time.sleep(self.delay_s)
        measurement = SimpleNamespace(
            accepted=True,
            angle_deg=12.5,
            status="accepted",
            n_snapshots=3,
            n_frames=20,
            component_std_deg=0.4,
            range_evidence=None,
            horizontal_deg=self.horizontal_deg,
            horizontal_confidence=0.9 if self.horizontal_deg is not None else None,
            horizontal_status="accepted" if self.horizontal_deg is not None else None,
            to_dict=lambda: {},
        )
        capture = SimpleNamespace(
            valid=True,
            sequence=1,
            path=None,
            raw=b"",
            dump_duration_s=0.01,
            error=None,
            trigger_timestamp=None,
            temperature_report=None,
        )
        self.finished.set()
        return SimpleNamespace(capture=capture, measurement=measurement, club_path=None)


class _SlowCameraRuntime:
    def __init__(self, delay_s: float):
        self.delay_s = delay_s
        self.calls = 0
        self.camera_analysis_eligible = True

    def capture_for_shot(self, _impact_timestamp, timeout_s=2.0):
        self.calls += 1
        time.sleep(self.delay_s)
        return None


class TestGatedPostprocessing:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        server_module._reset_shot_sequence()
        self.emitted = []
        self.logged = []
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "camera_replay_manager", None)
        monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
        monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
        monkeypatch.setattr(server_module, "ballistics_enabled", False)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "sim_connectors", [])
        monkeypatch.setattr(server_module, "_gated_stage_threads", {})
        monkeypatch.setattr(server_module, "_GATED_IWR6843_BUDGET_S", 0.05)
        monkeypatch.setattr(server_module, "_GATED_CAMERA_BUDGET_S", 0.05)
        monkeypatch.setattr(
            server_module,
            "get_session_logger",
            lambda: SimpleNamespace(
                log_shot=lambda shot, pipeline_ms=None: self.logged.append(
                    (shot.to_dict(), pipeline_ms)
                ),
                log_iwr6843_capture=lambda **_kwargs: None,
                log_camera_capture=lambda **_kwargs: None,
            ),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: self.emitted.append((event, payload)),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *args, **kwargs: threading.Thread(
                target=target, args=args, kwargs=kwargs, daemon=True
            ).start(),
        )
        yield
        _wait_for_shot_finalization_idle()
        for thread in list(server_module._gated_stage_threads.values()):
            thread.join(timeout=2.0)

    @staticmethod
    def _shot() -> Shot:
        return Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime(2026, 9, 30, 12, 0, 0),
            impact_timestamp=time.time(),
            club=ClubType.DRIVER,
            mode="rolling-buffer",
        )

    def _final_shot(self) -> dict:
        finals = [payload for event, payload in self.emitted if event == "shot_update"]
        assert len(finals) == 1
        return finals[0]["shot"]

    def test_off_waits_for_slow_iwr6843_and_keeps_its_angle(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.2)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", False)

        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()

        assert [event for event, _payload in self.emitted[:1]] == ["shot"]
        final = self._final_shot()
        assert final["launch_angle_vertical"] == 12.5
        assert final["launch_angle_vertical_source"] == "radar"
        assert "iwr6843_status" not in final
        assert runtime.finished.is_set()

    def test_on_skips_iwr6843_that_misses_its_budget(self, monkeypatch, caplog):
        runtime = _SlowIwrRuntime(delay_s=0.3)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)

        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="openflight.server"):
            on_shot_detected(self._shot())
            _wait_for_shot_finalization_idle()
        elapsed = time.monotonic() - started

        assert elapsed < 0.25, "final emit waited for the slow IWR6843 stage"
        final = self._final_shot()
        assert final["iwr6843_status"] == "skipped_budget"
        assert final["launch_angle_vertical_source"] == "estimated"
        assert self.logged[0][0]["iwr6843_status"] == "skipped_budget"
        assert self.logged[0][1]["iwr6843"] is None
        assert any("iwr6843 skipped" in r.getMessage() for r in caplog.records)
        runtime.finished.wait(2.0)

    def test_on_keeps_iwr6843_that_finishes_inside_its_budget(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.0)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)

        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()

        final = self._final_shot()
        assert final["launch_angle_vertical"] == 12.5
        assert "iwr6843_status" not in final
        assert self.logged[0][1]["iwr6843"] is not None
        assert final["latency_ms"]["iwr6843"] is not None

    def test_on_skips_camera_that_misses_its_budget_with_radar_fallback(self, monkeypatch):
        camera = _SlowCameraRuntime(delay_s=0.3)
        monkeypatch.setattr(server_module, "camera_capture_runtime", camera)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)

        started = time.monotonic()
        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()
        elapsed = time.monotonic() - started

        assert elapsed < 0.25, "final emit waited for the slow camera stage"
        final = self._final_shot()
        assert final["camera_status"] == "skipped_budget"
        assert final["experimental_camera_horizontal_status"].endswith(
            ":rejected_no_camera_capture"
        )
        assert final["experimental_fused_status"] == "rejected_no_camera_capture"
        assert self.logged[0][1]["camera_capture"] is None

    def test_off_waits_for_slow_camera(self, monkeypatch):
        camera = _SlowCameraRuntime(delay_s=0.2)
        monkeypatch.setattr(server_module, "camera_capture_runtime", camera)
        monkeypatch.setattr(server_module, "gated_postprocessing", False)

        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()

        final = self._final_shot()
        assert "camera_status" not in final
        assert self.logged[0][1]["camera_capture"] >= 200.0

    def test_next_shot_skips_stage_while_previous_run_is_still_busy(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.4)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)

        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()
        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()

        finals = [payload["shot"] for event, payload in self.emitted if event == "shot_update"]
        assert [shot["iwr6843_status"] for shot in finals] == ["skipped_budget", "skipped_budget"]
        assert runtime.calls == 1
        runtime.finished.wait(2.0)

    def test_late_stage_cannot_mutate_the_finalized_shot(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.2)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        shot = self._shot()

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()
        assert runtime.finished.wait(2.0)
        server_module._gated_stage_threads["iwr6843"].join(2.0)

        assert shot.launch_angle_vertical_source == "estimated"
        assert shot.iwr6843_status == "skipped_budget"

    def test_late_stage_cannot_write_roll_compensation_into_the_finalized_shot(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.2, horizontal_deg=3.0)
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        monkeypatch.setattr(server_module, "inclinometer_roll_compensation_enabled", True)
        shot = self._shot()
        shot.inclinometer = {"applied": True, "roll_deg": 2.0}

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()
        assert runtime.finished.wait(2.0)
        server_module._gated_stage_threads["iwr6843"].join(2.0)

        assert shot.iwr6843_status == "skipped_budget"
        assert shot.inclinometer == {"applied": True, "roll_deg": 2.0}
        assert self._final_shot()["inclinometer"] == {"applied": True, "roll_deg": 2.0}

    def test_late_iwr6843_stage_logs_and_reports_nothing(self, monkeypatch):
        runtime = _SlowIwrRuntime(delay_s=0.2)
        iwr_logs = []
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        monkeypatch.setattr(
            server_module,
            "get_session_logger",
            lambda: SimpleNamespace(
                log_shot=lambda shot, pipeline_ms=None: None,
                log_iwr6843_capture=lambda **kwargs: iwr_logs.append(kwargs),
                log_camera_capture=lambda **_kwargs: None,
            ),
        )
        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()
        server_module._gated_stage_threads["iwr6843"].join(2.0)

        assert self._final_shot()["iwr6843_status"] == "skipped_budget"
        assert iwr_logs == []
        assert [e for e, _p in self.emitted if e == "trigger_diagnostic_update"] == []

    def test_late_camera_stage_logs_nothing_for_the_finalized_shot(self, monkeypatch):
        camera = _SlowCameraRuntime(delay_s=0.2)
        camera_logs = []
        monkeypatch.setattr(server_module, "camera_capture_runtime", camera)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        monkeypatch.setattr(
            server_module,
            "get_session_logger",
            lambda: SimpleNamespace(
                log_shot=lambda shot, pipeline_ms=None: None,
                log_camera_capture=lambda **kwargs: camera_logs.append(kwargs),
            ),
        )
        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()
        server_module._gated_stage_threads["camera"].join(2.0)

        assert self._final_shot()["camera_status"] == "skipped_budget"
        assert camera_logs == []

    def test_on_time_camera_stage_still_logs_its_capture(self, monkeypatch):
        camera = _SlowCameraRuntime(delay_s=0.0)
        camera_logs = []
        monkeypatch.setattr(server_module, "camera_capture_runtime", camera)
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        monkeypatch.setattr(server_module, "_GATED_CAMERA_BUDGET_S", 1.0)
        monkeypatch.setattr(
            server_module,
            "get_session_logger",
            lambda: SimpleNamespace(
                log_shot=lambda shot, pipeline_ms=None: None,
                log_camera_capture=lambda **kwargs: camera_logs.append(kwargs),
            ),
        )
        on_shot_detected(self._shot())
        _wait_for_shot_finalization_idle()

        assert "camera_status" not in self._final_shot()
        assert [log["capture_error"] for log in camera_logs] == ["no_matching_camera_capture"]

    def test_stage_budgets_are_configurable(self, monkeypatch):
        from tests.test_server import _run_main_until_start_monitor

        _run_main_until_start_monitor(monkeypatch, [])
        assert server_module._GATED_IWR6843_BUDGET_S == pytest.approx(0.4)
        assert server_module._GATED_CAMERA_BUDGET_S == pytest.approx(0.4)
        _run_main_until_start_monitor(
            monkeypatch,
            ["--gated-iwr6843-budget-ms", "250", "--gated-camera-budget-ms", "2500"],
        )
        assert server_module._GATED_IWR6843_BUDGET_S == pytest.approx(0.25)
        assert server_module._GATED_CAMERA_BUDGET_S == pytest.approx(2.5)

    def test_gated_flag_default_is_off(self, monkeypatch):
        from tests.test_server import _run_main_until_start_monitor

        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        captured = _run_main_until_start_monitor(monkeypatch, [])
        assert captured
        assert server_module.gated_postprocessing is False

    def test_gated_flag_sets_the_module_switch(self, monkeypatch):
        from tests.test_server import _run_main_until_start_monitor

        monkeypatch.setattr(server_module, "gated_postprocessing", False)
        _run_main_until_start_monitor(monkeypatch, ["--gated-postprocessing"])
        assert server_module.gated_postprocessing is True
