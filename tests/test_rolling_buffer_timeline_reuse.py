"""Batched window FFT and reuse of the trigger's standard timeline."""

import numpy as np
import pytest

from openflight.rolling_buffer import IQCapture, RollingBufferProcessor, SpeedTimeline
from tests.spin_synth import synth_capture


def _legacy_window_loop(processor, capture, step_size):
    """The pre-vectorization per-window FFT loop, kept as a numerical reference."""
    i_data = np.asarray(capture.i_samples)
    q_data = np.asarray(capture.q_samples)
    readings = []
    start = 0
    while start + processor.WINDOW_SIZE <= len(i_data):
        i_block = i_data[start : start + processor.WINDOW_SIZE]
        q_block = q_data[start : start + processor.WINDOW_SIZE]
        i_centered = i_block - np.mean(i_block)
        q_centered = q_block - np.mean(q_block)
        i_scaled = i_centered * (processor.VOLTAGE_REF / processor.ADC_RANGE)
        q_scaled = q_centered * (processor.VOLTAGE_REF / processor.ADC_RANGE)
        complex_signal = (
            i_scaled * processor.hanning_window + 1j * q_scaled * processor.hanning_window
        )
        magnitude = np.abs(np.fft.fft(complex_signal, processor.FFT_SIZE))
        timestamp_ms = (start / processor.SAMPLE_RATE) * 1000
        for speed_mph, peak_mag, direction in processor._peaks_from_magnitude(magnitude):
            readings.append((timestamp_ms, direction, speed_mph, peak_mag))
        start += step_size
    return readings


def _as_tuples(timeline):
    return [(r.timestamp_ms, r.direction, r.speed_mph, r.magnitude) for r in timeline.readings]


def _assert_same_readings(actual, expected):
    assert len(actual) == len(expected)
    assert len(expected) > 0
    for (a_ts, a_dir, a_speed, a_mag), (e_ts, e_dir, e_speed, e_mag) in zip(actual, expected):
        assert a_ts == e_ts
        assert a_dir == e_dir
        assert a_speed == pytest.approx(e_speed, abs=1e-9)
        assert a_mag == pytest.approx(e_mag, abs=1e-9)


def _synth_iq_capture(*, integer_samples, **kwargs):
    i_samples, q_samples = synth_capture(9000.0, amplitude=400.0, **kwargs)
    if integer_samples:
        i_samples = [int(round(v)) for v in i_samples]
        q_samples = [int(round(v)) for v in q_samples]
    return IQCapture(sample_time=0.0, trigger_time=0.068, i_samples=i_samples, q_samples=q_samples)


@pytest.mark.parametrize("integer_samples", [True, False])
@pytest.mark.parametrize("step_attr", ["STEP_SIZE_STANDARD", "STEP_SIZE_OVERLAP"])
def test_batched_fft_matches_legacy_window_loop(integer_samples, step_attr):
    processor = RollingBufferProcessor()
    capture = _synth_iq_capture(integer_samples=integer_samples)
    step_size = getattr(processor, step_attr)

    timeline = processor._process_capture(capture, step_size)

    _assert_same_readings(_as_tuples(timeline), _legacy_window_loop(processor, capture, step_size))


def test_process_block_matches_batched_window():
    processor = RollingBufferProcessor()
    capture = _synth_iq_capture(integer_samples=True)
    start = 512
    i_block = np.asarray(capture.i_samples[start : start + processor.WINDOW_SIZE])
    q_block = np.asarray(capture.q_samples[start : start + processor.WINDOW_SIZE])

    (window_readings,) = processor._capture_window_readings(capture, [start])

    assert [
        (r.speed_mph, r.magnitude, r.direction) for r in window_readings
    ] == processor._process_block(i_block, q_block)


def test_capture_shorter_than_one_window_has_no_readings():
    processor = RollingBufferProcessor()
    capture = IQCapture(
        sample_time=0.0, trigger_time=0.0, i_samples=[2048] * 64, q_samples=[2048] * 64
    )

    standard, overlapping = processor._process_overlapping_with_standard(capture)

    assert standard.readings == []
    assert overlapping.readings == []


def _spy_fft(monkeypatch, processor):
    capture_window_readings = processor._capture_window_readings
    starts_seen = []

    def spy(capture, starts):
        starts_seen.extend(starts)
        return capture_window_readings(capture, starts)

    monkeypatch.setattr(processor, "_capture_window_readings", spy)
    return starts_seen


def test_process_capture_reuses_attached_standard_timeline(monkeypatch):
    processor = RollingBufferProcessor()
    capture = _synth_iq_capture(integer_samples=True)
    baseline_standard, baseline_overlapping = processor._process_overlapping_with_standard(
        _synth_iq_capture(integer_samples=True)
    )
    capture.standard_timeline = processor.process_standard(capture)
    starts_seen = _spy_fft(monkeypatch, processor)

    standard, overlapping = processor._process_overlapping_with_standard(capture)

    assert standard is capture.standard_timeline
    # No standard (128-sample-aligned) window is transformed a second time.
    assert starts_seen
    assert all(start % processor.STEP_SIZE_STANDARD != 0 for start in starts_seen)
    assert _as_tuples(standard) == _as_tuples(baseline_standard)
    assert _as_tuples(overlapping) == _as_tuples(baseline_overlapping)


def test_process_capture_with_attached_timeline_matches_fresh_processing(monkeypatch):
    processor = RollingBufferProcessor()
    fresh = processor.process_capture(_synth_iq_capture(integer_samples=True))

    capture = _synth_iq_capture(integer_samples=True)
    capture.standard_timeline = processor.process_standard(capture)
    starts_seen = _spy_fft(monkeypatch, processor)
    reused = processor.process_capture(capture)

    assert fresh is not None and reused is not None
    assert all(start % processor.STEP_SIZE_STANDARD != 0 for start in starts_seen)
    assert reused.ball_speed_mph == fresh.ball_speed_mph
    assert reused.ball_timestamp_ms == fresh.ball_timestamp_ms
    assert reused.club_speed_mph == fresh.club_speed_mph
    assert _as_tuples(reused.timeline) == _as_tuples(fresh.timeline)


def test_timeline_attached_to_a_different_capture_is_not_reused(monkeypatch):
    processor = RollingBufferProcessor()
    capture = _synth_iq_capture(integer_samples=True)
    other = _synth_iq_capture(integer_samples=True, seed=7)
    capture.standard_timeline = processor.process_standard(other)
    starts_seen = _spy_fft(monkeypatch, processor)

    standard, _overlapping = processor._process_overlapping_with_standard(capture)

    assert standard is not capture.standard_timeline
    assert any(start % processor.STEP_SIZE_STANDARD == 0 for start in starts_seen)


def test_capture_repr_and_equality_ignore_attached_timeline():
    capture = _synth_iq_capture(integer_samples=True)
    twin = IQCapture(
        sample_time=capture.sample_time,
        trigger_time=capture.trigger_time,
        i_samples=list(capture.i_samples),
        q_samples=list(capture.q_samples),
        timestamp=capture.timestamp,
    )
    capture.standard_timeline = SpeedTimeline(readings=[], sample_rate_hz=234.375, capture=capture)

    assert capture == twin
    assert "standard_timeline" not in repr(capture)
