"""--spin-harmonic-fit: harmonic-series spin for marker-pattern (RCT) balls."""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.rolling_buffer import IQCapture, RollingBufferMonitor, RollingBufferProcessor
from openflight.rolling_buffer.harmonic import estimate_harmonic_spin

SAMPLE_RATE_HZ = 30_000
# 8-iron shots hit with a Titleist RCT ball on 2026-10-05, SkyTrak alongside.
RCT_SAMPLE = (
    Path(__file__).parent.parent / "session_logs" / "session_20261005_rct_8iron_sample.jsonl"
)
SHOT_14_SKYTRAK_SPIN_RPM = 5190.0


def _envelope(harmonic_depths, spin_rpm, *, duration_ms=45.0, noise_rms=0.0, seed=1):
    """Ball envelope with ``{harmonic: depth}`` lines of ``spin_rpm``."""
    time_s = np.arange(int(duration_ms * SAMPLE_RATE_HZ / 1000)) / SAMPLE_RATE_HZ
    envelope = np.ones_like(time_s)
    for harmonic, depth in harmonic_depths.items():
        envelope += depth * np.sin(2 * np.pi * harmonic * spin_rpm / 60.0 * time_s + harmonic)
    envelope += np.random.default_rng(seed).normal(0.0, noise_rms, len(time_s))
    return 400.0 * envelope


def _capture(envelope, ball_speed_mph=100.0):
    """I/Q capture whose ball tone carries ``envelope`` from 68 ms on."""
    time_s = np.arange(4096) / SAMPLE_RATE_HZ
    doppler_hz = (
        2.0
        * (ball_speed_mph / RollingBufferProcessor.MPS_TO_MPH)
        / RollingBufferProcessor.WAVELENGTH_M
    )
    amplitude = np.zeros(4096)
    start = int(0.068 * SAMPLE_RATE_HZ)
    amplitude[start : start + len(envelope)] = envelope[: 4096 - start]
    phase = 2.0 * np.pi * doppler_hz * time_s
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=(amplitude * np.cos(phase) + 2048.0).astype(int).tolist(),
        q_samples=(amplitude * np.sin(phase) + 2048.0).astype(int).tolist(),
    )


def _recorded_shot(shot_number):
    for line in RCT_SAMPLE.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["shot_number"] == shot_number:
            capture = IQCapture(
                sample_time=0.0,
                trigger_time=0.068,
                i_samples=entry["i_samples"],
                q_samples=entry["q_samples"],
            )
            return capture, entry["ball_speed_mph"], entry["ball_timestamp_ms"]
    raise AssertionError(f"shot {shot_number} missing from {RCT_SAMPLE}")


def test_weak_fundamental_under_a_strong_second_harmonic_is_the_spin():
    estimate = estimate_harmonic_spin(
        _envelope({1: 0.05, 2: 0.3, 3: 0.15}, 4800.0, noise_rms=0.02), SAMPLE_RATE_HZ
    )

    assert estimate.spin_rpm == pytest.approx(4800.0, rel=0.03)
    assert estimate.fit > 0.75


def test_single_line_is_not_halved():
    estimate = estimate_harmonic_spin(_envelope({1: 0.3}, 7200.0, noise_rms=0.02), SAMPLE_RATE_HZ)

    assert estimate.spin_rpm == pytest.approx(7200.0, rel=0.03)


def test_noise_envelope_fits_poorly():
    estimate = estimate_harmonic_spin(_envelope({}, 5000.0, noise_rms=0.2), SAMPLE_RATE_HZ)

    assert estimate.fit < RollingBufferProcessor.HARMONIC_FIT_MIN


def test_window_shorter_than_two_cycles_of_the_top_spin_is_an_error():
    with pytest.raises(ValueError, match="too short"):
        estimate_harmonic_spin(_envelope({1: 0.3}, 6000.0, duration_ms=8.0), SAMPLE_RATE_HZ)


def test_recorded_rct_shot_reads_the_fundamental_the_envelope_fft_doubles():
    capture, ball_speed_mph, ball_timestamp_ms = _recorded_shot(14)

    doubled = RollingBufferProcessor(ball_marker="rct").detect_spin(
        capture, ball_speed_mph, ball_timestamp_ms
    )
    spin = RollingBufferProcessor(ball_marker="rct", spin_harmonic_fit=True).detect_spin_harmonic(
        capture, ball_speed_mph, ball_timestamp_ms
    )

    assert doubled.spin_rpm == pytest.approx(2 * SHOT_14_SKYTRAK_SPIN_RPM, rel=0.05)
    assert spin.spin_rpm == pytest.approx(SHOT_14_SKYTRAK_SPIN_RPM, rel=0.05)
    assert spin.is_reliable
    assert spin.method == "harmonic_fit+marker_rct"


def test_recorded_rct_shot_with_a_split_spectrum_is_rejected():
    capture, ball_speed_mph, ball_timestamp_ms = _recorded_shot(7)

    spin = RollingBufferProcessor(spin_harmonic_fit=True).detect_spin_harmonic(
        capture, ball_speed_mph, ball_timestamp_ms
    )

    assert spin.spin_rpm == 0
    assert "Harmonic fit too weak" in spin.rejection_reason
    assert spin.method == "harmonic_fit"


def test_short_ball_signal_is_rejected():
    spin = RollingBufferProcessor(spin_harmonic_fit=True).detect_spin_harmonic(
        _capture(_envelope({1: 0.3}, 6000.0, duration_ms=12.0)), 100.0, 68.0
    )

    assert spin.spin_rpm == 0
    assert "Ball signal too short" in spin.rejection_reason


@pytest.mark.parametrize(
    ("spin_harmonic_fit", "estimator"), [(False, "envelope_fft"), (True, "harmonic_fit")]
)
def test_process_capture_uses_the_harmonic_estimator_only_when_enabled(
    spin_harmonic_fit, estimator
):
    processor = RollingBufferProcessor(ball_marker="rct", spin_harmonic_fit=spin_harmonic_fit)

    processed = processor.process_capture(
        _capture(_envelope({1: 0.1, 2: 0.3}, 4800.0, duration_ms=60.0))
    )

    assert processed is not None
    assert processed.spin.estimator == estimator


def test_monitor_plumbs_spin_harmonic_fit():
    assert RollingBufferMonitor(port=None).processor.spin_harmonic_fit is False
    assert RollingBufferMonitor(port=None, spin_harmonic_fit=True).processor.spin_harmonic_fit
