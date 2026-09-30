"""Live-pipeline tests for the ungated multitaper spin estimator."""

from datetime import datetime

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer import (
    IQCapture,
    ProcessedCapture,
    RollingBufferProcessor,
    SpeedTimeline,
    SpinResult,
)
from openflight.rolling_buffer.multitaper import repair_clipped_iq


def _modulated_capture(
    *,
    ball_speed_mph: float = 100.0,
    spin_rpm: float = 9000.0,
    fade_hz: float = 48.0,
    spin_depth: float = 0.015,
    fade_depth: float = 0.15,
    sample_count: int = 4096,
) -> IQCapture:
    """Build an OPS capture with strong multipath fade and weaker seam tone."""
    sample_rate_hz = 30_000
    time_s = np.arange(sample_count, dtype=np.float64) / sample_rate_hz
    speed_mps = ball_speed_mph / RollingBufferProcessor.MPS_TO_MPH
    doppler_hz = 2.0 * speed_mps / RollingBufferProcessor.WAVELENGTH_M
    spin_hz = spin_rpm / 60.0
    amplitude = 500.0 * (
        1.0
        + fade_depth * np.sin(2.0 * np.pi * fade_hz * time_s)
        + spin_depth * np.sin(2.0 * np.pi * spin_hz * time_s)
    )
    phase = 2.0 * np.pi * doppler_hz * time_s
    i_samples = np.clip(amplitude * np.cos(phase) + 2048.0, 0, 4095).astype(int)
    q_samples = np.clip(amplitude * np.sin(phase) + 2048.0, 0, 4095).astype(int)
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=i_samples.tolist(),
        q_samples=q_samples.tolist(),
    )


def test_multitaper_recovers_weak_spin_below_strong_fade():
    """Fade regression should expose a seam tone much weaker than multipath."""
    processor = RollingBufferProcessor()

    result = processor.detect_spin_multitaper(
        _modulated_capture(),
        ball_speed_mph=100.0,
        ball_timestamp_ms=5.0,
    )

    assert result.method == "multitaper_ungated"
    assert result.spin_rpm == pytest.approx(9000.0, abs=500.0)
    assert result.quality == "experimental"
    assert result.rejection_reason is None


def test_clipped_iq_is_interpolated_before_multitaper_processing():
    """Near-rail samples must receive the same repair used in offline scoring."""
    i_samples = np.array([1000.0, 1100.0, 4095.0, 1300.0, 1400.0])
    q_samples = np.array([2000.0, 2100.0, 2200.0, 2300.0, 2400.0])

    repaired, clipped_fraction = repair_clipped_iq(i_samples, q_samples)

    assert clipped_fraction == pytest.approx(0.2)
    assert repaired.real[2] == pytest.approx(0.0)
    assert repaired.imag[2] == pytest.approx(0.0)


def test_process_capture_uses_multitaper_estimator(monkeypatch):
    """The production capture path must call multitaper, not the legacy gate."""
    processor = RollingBufferProcessor()

    def legacy_detector_must_not_run(*args, **kwargs):
        raise AssertionError("legacy spin detector called")

    monkeypatch.setattr(processor, "detect_spin", legacy_detector_must_not_run)
    processed = processor.process_capture(_modulated_capture())

    assert processed is not None
    assert processed.spin is not None
    assert processed.spin.method == "multitaper_ungated"
    assert processed.spin.spin_rpm > 0


def _spy_fft_windows(monkeypatch, processor):
    """Record the window start samples sent through the batched FFT."""
    capture_window_readings = processor._capture_window_readings
    window_magnitudes = processor._window_magnitudes
    starts_seen = []
    fft_rows = []

    def spy_readings(capture, starts):
        starts_seen.extend(starts)
        return capture_window_readings(capture, starts)

    def spy_magnitudes(i_blocks, q_blocks):
        fft_rows.append(len(i_blocks))
        return window_magnitudes(i_blocks, q_blocks)

    monkeypatch.setattr(processor, "_capture_window_readings", spy_readings)
    monkeypatch.setattr(processor, "_window_magnitudes", spy_magnitudes)
    return starts_seen, fft_rows


def _overlapping_window_count(processor, sample_count=4096):
    return ((sample_count - processor.WINDOW_SIZE) // processor.STEP_SIZE_OVERLAP) + 1


def test_process_capture_does_not_repeat_standard_fft_windows(monkeypatch):
    """Standard windows are a subset of the overlapping FFT timeline."""
    processor = RollingBufferProcessor()
    starts_seen, fft_rows = _spy_fft_windows(monkeypatch, processor)

    assert processor.process_capture(_modulated_capture()) is not None
    expected_overlapping_windows = _overlapping_window_count(processor)
    assert sum(fft_rows) == expected_overlapping_windows
    assert len(set(starts_seen)) == len(starts_seen) == expected_overlapping_windows


def test_multitaper_accepts_short_record_used_by_offline_scoring():
    """The live wrapper must not reintroduce the legacy 600-sample gate."""
    processor = RollingBufferProcessor()

    result = processor.detect_spin_multitaper(
        _modulated_capture(sample_count=512),
        ball_speed_mph=100.0,
        ball_timestamp_ms=0.0,
    )

    assert result.method == "multitaper_ungated"
    assert result.spin_rpm > 0
    assert result.rejection_reason is None


def test_multitaper_candidate_is_reported_without_confidence_or_rail_gate():
    """Experimental candidates stay visible but remain excluded from carry."""
    from openflight.rolling_buffer import RollingBufferMonitor

    capture = _modulated_capture()
    processed = ProcessedCapture(
        timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
        ball_speed_mph=100.0,
        ball_timestamp_ms=60.0,
        club_speed_mph=75.0,
        spin=SpinResult(
            spin_rpm=10_950.0,
            confidence=0.1,
            snr=1.2,
            quality="experimental",
            method="multitaper_ungated",
            multipath_fade_hz=48.2,
            peak_freq_hz=182.5,
            at_upper_rail=True,
        ),
        capture=capture,
    )
    monitor = RollingBufferMonitor(port=None, trigger_type="sound")
    monitor.set_club(ClubType.PW)

    shot = monitor._create_shot(processed)

    assert shot is not None
    assert shot.spin_rpm == pytest.approx(10_950.0)
    assert shot.spin_method == "multitaper_ungated"
    assert shot.spin_confidence == pytest.approx(0.1)
    assert shot.spin_quality == "experimental"
    assert shot.spin_rejection_reason is None
    assert shot.carry_spin_adjusted is None

    from openflight.server import shot_to_dict

    payload = shot_to_dict(shot)
    assert payload["spin_rpm"] == 10_950
    assert payload["spin_method"] == "multitaper_ungated"
    assert payload["spin_quality"] == "experimental"
    assert payload["spin_multipath_fade_hz"] == pytest.approx(48.2)


def test_shot_method_defaults_to_none():
    """Non-rolling and legacy shots remain backward compatible."""
    from openflight.launch_monitor import Shot

    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

    assert shot.spin_method is None


def _assert_untrusted_confidence(result):
    from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, SPIN_CONFIDENCE_RELIABLE

    assert result.method == "multitaper_ungated"
    assert 0.0 <= result.confidence <= RollingBufferProcessor.MULTITAPER_MAX_CONFIDENCE
    assert result.confidence < SPIN_CONFIDENCE_RELIABLE
    assert result.confidence < SPIN_CONFIDENCE_HIGH
    assert not result.is_reliable


def test_multitaper_confidence_stays_low_for_a_clean_candidate():
    """r ~ 0.19 vs TrackMan: even a clear candidate must not look trusted."""
    result = RollingBufferProcessor().detect_spin_multitaper(
        _modulated_capture(spin_depth=0.05, fade_depth=0.0),
        ball_speed_mph=100.0,
        ball_timestamp_ms=5.0,
    )

    assert result.spin_rpm > 0
    _assert_untrusted_confidence(result)


@pytest.mark.parametrize("peak_to_floor", [0.0, 5.0, 1e3, 1e12])
def test_multitaper_confidence_is_bounded_for_any_evidence(monkeypatch, peak_to_floor):
    from openflight.rolling_buffer import processor as processor_module
    from openflight.rolling_buffer.multitaper import MultitaperEstimate

    monkeypatch.setattr(
        processor_module,
        "estimate_multitaper_spin",
        lambda *_args, **_kwargs: MultitaperEstimate(
            spin_hz=100.0, spin_rpm=6000.0, peak_to_floor=peak_to_floor, fade_hz=40.0
        ),
    )

    result = RollingBufferProcessor().detect_spin_multitaper(
        _modulated_capture(), ball_speed_mph=100.0, ball_timestamp_ms=5.0
    )

    _assert_untrusted_confidence(result)
