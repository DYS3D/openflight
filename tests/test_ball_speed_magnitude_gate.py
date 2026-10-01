"""Opt-in magnitude gate on the ball-speed picker (weak clipping aliases)."""

import numpy as np
import pytest

from openflight.rolling_buffer import IQCapture, RollingBufferMonitor, RollingBufferProcessor
from tests.spin_synth import ADC_CENTER, synth_capture


def _ball_with_alias(alias_amplitude: float) -> IQCapture:
    """A strong 100 mph ball plus a persistent weaker return at 117 mph."""
    ball_i, ball_q = synth_capture(
        rpm=3000, ball_speed_mph=100.0, amplitude=1800.0, decel_mph_per_s=0.0
    )
    alias_i, alias_q = synth_capture(
        rpm=3000,
        ball_speed_mph=117.0,
        amplitude=alias_amplitude,
        decel_mph_per_s=0.0,
        noise_rms=0.0,
        seed=2,
    )
    i_samples = np.array(ball_i) + np.array(alias_i) - ADC_CENTER
    q_samples = np.array(ball_q) + np.array(alias_q) - ADC_CENTER
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.0,
        i_samples=i_samples.tolist(),
        q_samples=q_samples.tolist(),
    )


def _ball_speed(processor: RollingBufferProcessor, capture: IQCapture) -> float:
    processed = processor.process_capture(capture)
    assert processed is not None
    return processed.ball_speed_mph


def test_default_picks_the_fastest_repeated_bin_even_when_weak():
    assert _ball_speed(RollingBufferProcessor(), _ball_with_alias(80.0)) == pytest.approx(
        117.0, abs=1.0
    )


def test_gate_rejects_a_weak_alias_above_the_ball():
    processor = RollingBufferProcessor(ball_speed_magnitude_gate=True)

    assert _ball_speed(processor, _ball_with_alias(80.0)) == pytest.approx(100.0, abs=1.0)


def test_gate_keeps_a_faster_return_above_the_magnitude_fraction():
    processor = RollingBufferProcessor(ball_speed_magnitude_gate=True)

    assert _ball_speed(processor, _ball_with_alias(400.0)) == pytest.approx(117.0, abs=1.0)


def test_gate_leaves_a_clean_capture_unchanged():
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=100.0, amplitude=1800.0)
    capture = IQCapture(sample_time=0.0, trigger_time=0.0, i_samples=i_samples, q_samples=q_samples)

    assert _ball_speed(RollingBufferProcessor(ball_speed_magnitude_gate=True), capture) == (
        _ball_speed(RollingBufferProcessor(), capture)
    )


def test_monitor_plumbs_ball_speed_magnitude_gate():
    assert RollingBufferMonitor(port=None).processor.ball_speed_magnitude_gate is False
    monitor = RollingBufferMonitor(port=None, ball_speed_magnitude_gate=True)
    assert monitor.processor.ball_speed_magnitude_gate is True
