"""--ball-marker and --spin-octave-check: 1x/2x spin handling on the OPS path.

Both options are off by default and must then reproduce the pre-change spin
output exactly. REAL_CAPTURE_GOLDEN_SPIN holds the (rpm, confidence) that
process_capture() returned for each recorded shot at 7e63e74.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from spin_synth import synth_capture

from openflight import rolling_buffer as rolling_buffer_package, server as server_module
from openflight.clubs import ClubType
from openflight.clubs.physics import get_plausible_spin_rpm
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, spin_is_trusted
from openflight.rolling_buffer import (
    IQCapture,
    ProcessedCapture,
    RollingBufferMonitor,
    RollingBufferProcessor,
    SpeedTimeline,
    SpinResult,
)
from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed
from openflight.rolling_buffer.types import spin_method_name

SESSION_LOG = Path(__file__).parent.parent / "session_logs" / "session_20260501_180406_range.jsonl"

REAL_CAPTURE_GOLDEN_SPIN = {
    1: (7580.56640625, 0.080841746636207),
    2: (10986.328125, 0.053545016111876245),
    3: (7031.25, 0.07830318533671572),
    4: (7360.83984375, 0.05088454546094506),
    5: (10986.328125, 0.05141497694646293),
    6: (8349.609375, 0.027833304243935222),
    7: (9558.10546875, 0.09650055221464221),
    8: (10986.328125, 0.1549231542713217),
    9: (9887.6953125, 0.06916615185759172),
}


def _am_capture(tones, *, ball_speed_mph=120.0, fade_hz=None, fade_depth=0.0):
    """Ball return whose amplitude is modulated at each ``{rpm: depth}`` tone."""
    sample_rate_hz = 30_000
    time_s = np.arange(4096) / sample_rate_hz
    doppler_hz = (
        2.0
        * (ball_speed_mph / RollingBufferProcessor.MPS_TO_MPH)
        / RollingBufferProcessor.WAVELENGTH_M
    )
    amplitude = np.ones_like(time_s)
    for rpm, depth in tones.items():
        amplitude += depth * np.sin(2.0 * np.pi * rpm / 60.0 * time_s)
    if fade_hz is not None:
        amplitude += fade_depth * np.sin(2.0 * np.pi * fade_hz * time_s)
    amplitude *= 400.0
    phase = 2.0 * np.pi * doppler_hz * time_s
    i_samples = np.clip(amplitude * np.cos(phase) + 2048.0, 0, 4095).astype(int)
    q_samples = np.clip(amplitude * np.sin(phase) + 2048.0, 0, 4095).astype(int)
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=i_samples.tolist(),
        q_samples=q_samples.tolist(),
    )


def _line_index(freqs, rpm):
    return int(np.argmin(np.abs(freqs - rpm / 60.0)))


def _line_spectrum(lines):
    """Envelope-FFT-like magnitude with a narrow line at each ``{rpm: magnitude}``."""
    freqs = np.arange(33.0, 200.0, 30_000 / 8192)
    magnitude = np.full(len(freqs), 0.01)
    for rpm, peak in lines.items():
        idx = _line_index(freqs, rpm)
        magnitude[idx] = peak
        magnitude[idx - 1] = magnitude[idx + 1] = peak / 2
    return magnitude, freqs


def _spin_fields(spin: SpinResult) -> tuple:
    return (
        spin.spin_rpm,
        spin.confidence,
        spin.snr,
        spin.quality,
        spin.method,
        spin.rejection_reason,
        [candidate.to_dict() for candidate in spin.candidates],
    )


def test_default_options_are_off():
    processor = RollingBufferProcessor()
    monitor = RollingBufferMonitor(port=None, trigger_type="sound")

    assert processor.ball_marker == "none"
    assert processor.spin_octave_check is False
    assert monitor.processor.ball_marker == "none"
    assert monitor.processor.spin_octave_check is False
    assert processor.spin_octave_prior == "optimal"
    assert monitor.processor.spin_octave_prior == "optimal"


def test_invalid_ball_marker_is_rejected():
    with pytest.raises(ValueError, match="ball_marker"):
        RollingBufferProcessor(ball_marker="stripe")


def test_flags_off_matches_pre_change_output_on_synthetic_signal():
    i_samples, q_samples = synth_capture(
        6500, ball_speed_mph=120.0, amplitude=400.0, noise_rms=10.0, seed=4
    )
    capture = IQCapture(0.0, 0.068, i_samples, q_samples)

    for processor in (
        RollingBufferProcessor(),
        RollingBufferProcessor(ball_marker="none", spin_octave_check=False),
    ):
        processed = processor.process_capture(
            capture, expected_spin_for_ball_speed=lambda _speed: 7000.0
        )
        live = processed.spin
        assert live.spin_rpm == 6481.93359375
        assert live.confidence == pytest.approx(0.19225480598114097, rel=1e-12)
        assert live.snr == pytest.approx(124.1123240508971, rel=1e-12)
        assert live.multipath_fade_hz == 84.9716796875
        assert live.quality == "experimental"
        assert live.method == "multitaper_ungated"
        assert live.rejection_reason is None
        assert not live.octave_corrected

        envelope = processor.detect_spin(
            capture,
            processed.ball_speed_mph,
            processed.ball_timestamp_ms,
            expected_spin_rpm=7000.0,
        )
        assert (envelope.spin_rpm, envelope.confidence, envelope.snr) == (6372, 0.9, 25.73)
        assert (envelope.quality, envelope.method) == ("high", "envelope_fft")
        assert [c.to_dict()["rpm"] for c in envelope.candidates] == [6372, 7690, 9888, 8350, 2417]


def _recorded_captures() -> dict[int, IQCapture]:
    captures = {}
    with SESSION_LOG.open() as log:
        for line in log:
            entry = json.loads(line)
            if entry["type"] == "rolling_buffer_capture":
                captures[entry["shot_number"]] = IQCapture(
                    sample_time=entry["sample_time"],
                    trigger_time=entry["trigger_time"],
                    i_samples=entry["i_samples"],
                    q_samples=entry["q_samples"],
                )
    return captures


RECORDED_CAPTURES = _recorded_captures()


@pytest.mark.parametrize("shot_number", sorted(REAL_CAPTURE_GOLDEN_SPIN))
def test_flags_off_matches_pre_change_output_on_recorded_captures(shot_number):
    capture = RECORDED_CAPTURES[shot_number]
    default = RollingBufferProcessor().process_capture(capture)
    explicit_off = RollingBufferProcessor(ball_marker="none", spin_octave_check=False)
    flags_off = explicit_off.process_capture(capture)

    rpm, confidence = REAL_CAPTURE_GOLDEN_SPIN[shot_number]
    assert default.spin.spin_rpm == rpm
    assert default.spin.confidence == pytest.approx(confidence, rel=1e-12)
    assert default.spin.method == "multitaper_ungated"
    assert default.spin.quality == "experimental"
    assert default.spin.rejection_reason is None
    assert _spin_fields(flags_off.spin) == _spin_fields(default.spin)

    prior = 3000.0
    assert _spin_fields(
        explicit_off.detect_spin(capture, default.ball_speed_mph, default.ball_timestamp_ms, prior)
    ) == _spin_fields(
        RollingBufferProcessor().detect_spin(
            capture, default.ball_speed_mph, default.ball_timestamp_ms, prior
        )
    )


def test_marker_prefers_fundamental_over_stronger_second_harmonic():
    capture = _am_capture({4200: 0.03, 8400: 0.05})

    unmarked = RollingBufferProcessor().detect_spin(capture, 120.0, 5.0)
    marked = RollingBufferProcessor(ball_marker="dot").detect_spin(capture, 120.0, 5.0)

    assert unmarked.spin_rpm == pytest.approx(8400, rel=0.02)
    assert marked.spin_rpm == pytest.approx(4200, rel=0.02)
    assert marked.method == "envelope_fft+marker_dot"
    assert marked.estimator == "envelope_fft"
    assert not marked.octave_corrected
    assert not marked.is_reliable, "1x weaker than 2x must fail the persistence check"


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param({6000: 1.0}, id="no-half-line"),
        pytest.param({3000: 0.3, 6000: 1.0}, id="half-line-too-weak"),
        pytest.param({3500: 0.8, 6000: 1.0}, id="strong-line-outside-tolerance"),
    ],
)
def test_marker_keeps_the_pick_without_a_supported_half_frequency_line(lines):
    magnitude, freqs = _line_spectrum(lines)
    processor = RollingBufferProcessor(ball_marker="rct")
    pick = int(np.argmax(magnitude))

    assert processor._prefer_marker_fundamental(magnitude, freqs, pick) == pick


def test_marker_moves_a_2x_pick_to_its_half_frequency_line():
    magnitude, freqs = _line_spectrum({3000: 0.5, 6000: 1.0})
    processor = RollingBufferProcessor(ball_marker="dot")

    idx = processor._prefer_marker_fundamental(magnitude, freqs, int(np.argmax(magnitude)))

    assert idx == _line_index(freqs, 3000)


def test_marker_mode_routes_live_spin_to_the_gated_envelope_estimator(monkeypatch):
    processor = RollingBufferProcessor(ball_marker="rct")

    def multitaper_must_not_run(*_args, **_kwargs):
        raise AssertionError("multitaper spin used in marker mode")

    monkeypatch.setattr(processor, "detect_spin_multitaper", multitaper_must_not_run)
    i_samples, q_samples = synth_capture(
        6500, ball_speed_mph=120.0, amplitude=400.0, noise_rms=10.0, seed=4
    )
    processed = processor.process_capture(
        IQCapture(0.0, 0.068, i_samples, q_samples),
        expected_spin_for_ball_speed=lambda _speed: 7000.0,
    )

    assert processed.spin.method == "envelope_fft+marker_rct"


def _marked_shot(spin: SpinResult) -> object:
    processed = ProcessedCapture(
        timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
        ball_speed_mph=120.0,
        ball_timestamp_ms=5.0,
        club_speed_mph=90.0,
        spin=spin,
    )
    monitor = RollingBufferMonitor(port=None, trigger_type="sound", ball_marker="dot")
    monitor.set_club(ClubType.IRON_5)
    return monitor._create_shot(processed)


def test_marker_spin_reaches_trusted_band_when_checks_pass():
    capture = _am_capture({5000: 0.04})

    marked = RollingBufferProcessor(ball_marker="dot").detect_spin(
        capture, 120.0, 5.0, expected_spin_rpm=5200.0
    )
    unmarked = RollingBufferProcessor().detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=5200.0
    )

    assert marked.spin_rpm == pytest.approx(5000, rel=0.02)
    assert marked.quality == "high"
    assert marked.confidence >= SPIN_CONFIDENCE_HIGH
    assert marked.is_reliable
    shot = _marked_shot(marked)
    assert shot.spin_method == "envelope_fft+marker_dot"
    assert spin_is_trusted(shot)

    assert unmarked.confidence < SPIN_CONFIDENCE_HIGH
    assert not unmarked.is_reliable


@pytest.mark.parametrize("expected_spin_rpm", [11_500.0, None])
def test_marker_spin_outside_club_range_stays_untrusted(expected_spin_rpm):
    capture = _am_capture({5000: 0.04})

    marked = RollingBufferProcessor(ball_marker="dot").detect_spin(
        capture, 120.0, 5.0, expected_spin_rpm=expected_spin_rpm
    )

    assert marked.spin_rpm == pytest.approx(5000, rel=0.02)
    assert marked.confidence <= 0.5
    assert marked.quality == "low"
    assert not spin_is_trusted(_marked_shot(marked))


def test_octave_check_halves_a_2x_multitaper_pick():
    capture = _am_capture({4200: 0.03, 8400: 0.05}, fade_hz=48.0, fade_depth=0.15)

    off = RollingBufferProcessor().detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=4300.0
    )
    on = RollingBufferProcessor(spin_octave_check=True).detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=4300.0
    )

    assert off.spin_rpm == pytest.approx(8400, rel=0.02)
    assert off.method == "multitaper_ungated"
    assert on.spin_rpm == pytest.approx(4200, rel=0.02)
    assert on.method == "multitaper_ungated+octave_halved"
    assert on.octave_corrected
    assert on.estimator == "multitaper_ungated"
    assert on.quality == "experimental"
    assert on.confidence <= RollingBufferProcessor.MULTITAPER_MAX_CONFIDENCE


def test_octave_check_doubles_a_half_multitaper_pick():
    capture = _am_capture({4200: 0.05, 8400: 0.03}, fade_hz=48.0, fade_depth=0.15)

    off = RollingBufferProcessor().detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=8200.0
    )
    on = RollingBufferProcessor(spin_octave_check=True).detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=8200.0
    )

    assert off.spin_rpm == pytest.approx(4200, rel=0.02)
    assert on.spin_rpm == pytest.approx(8400, rel=0.02)
    assert on.method == "multitaper_ungated+octave_doubled"
    assert on.octave_corrected


def test_octave_check_keeps_a_2x_pick_without_a_supporting_candidate():
    capture = _am_capture({8400: 0.05}, fade_hz=48.0, fade_depth=0.15)

    on = RollingBufferProcessor(spin_octave_check=True).detect_spin_multitaper(
        capture, 120.0, 5.0, expected_spin_rpm=4300.0
    )

    assert on.spin_rpm == pytest.approx(8400, rel=0.02)
    assert on.method == "multitaper_ungated"
    assert not on.octave_corrected


@pytest.mark.parametrize(
    ("lines", "expected_spin_rpm", "corrected_rpm", "correction"),
    [
        pytest.param({3000: 0.5, 6000: 1.0}, 3000.0, 3000, "halved", id="halved"),
        pytest.param({3000: 1.0, 6000: 0.5}, 6000.0, 6000, "doubled", id="doubled"),
        pytest.param({3000: 0.3, 6000: 1.0}, 3000.0, 6000, None, id="half-line-too-weak"),
        pytest.param({6000: 1.0}, 3000.0, 6000, None, id="no-half-line"),
        pytest.param({3000: 0.5, 6000: 1.0}, 4500.0, 6000, None, id="ratio-not-an-octave"),
        pytest.param({3000: 0.5, 6000: 1.0}, None, 6000, None, id="no-prior"),
    ],
)
def test_octave_corrected_pick_on_line_spectra(lines, expected_spin_rpm, corrected_rpm, correction):
    magnitude, freqs = _line_spectrum(lines)
    processor = RollingBufferProcessor(spin_octave_check=True)

    idx, applied = processor._octave_corrected_pick(
        magnitude, freqs, int(np.argmax(magnitude)), expected_spin_rpm
    )

    assert idx == _line_index(freqs, corrected_rpm)
    assert applied == correction


def test_octave_check_is_inert_when_the_estimate_has_no_spectrum(monkeypatch):
    from openflight.rolling_buffer import processor as processor_module
    from openflight.rolling_buffer.multitaper import MultitaperEstimate

    monkeypatch.setattr(
        processor_module,
        "estimate_multitaper_spin",
        lambda *_args, **_kwargs: MultitaperEstimate(
            spin_hz=140.0, spin_rpm=8400.0, peak_to_floor=50.0, fade_hz=48.0
        ),
    )

    result = RollingBufferProcessor(spin_octave_check=True).detect_spin_multitaper(
        _am_capture({8400: 0.05}), 120.0, 5.0, expected_spin_rpm=4200.0
    )

    assert result.spin_rpm == 8400.0
    assert result.method == "multitaper_ungated"


def test_marker_disables_doubling_but_keeps_halving():
    doubling_lines = {3000: 1.0, 6000: 0.5}
    magnitude, freqs = _line_spectrum(doubling_lines)
    pick = int(np.argmax(magnitude))
    marked = RollingBufferProcessor(ball_marker="dot", spin_octave_check=True)

    assert marked._octave_corrected_pick(magnitude, freqs, pick, 6000.0) == (pick, None)

    magnitude, freqs = _line_spectrum({3000: 0.5, 6000: 1.0})
    idx, applied = marked._octave_corrected_pick(
        magnitude, freqs, int(np.argmax(magnitude)), 3000.0
    )
    assert idx == _line_index(freqs, 3000)
    assert applied == "halved"


def test_marker_keeps_the_fundamental_when_the_prior_points_at_2x():
    """The prior selects the 2x line; the marker moves back and doubling stays off."""
    capture = _am_capture({4200: 0.05, 8400: 0.03})

    unmarked = RollingBufferProcessor(spin_octave_check=True).detect_spin(
        capture, 120.0, 5.0, expected_spin_rpm=8200.0
    )
    marked = RollingBufferProcessor(ball_marker="rct", spin_octave_check=True).detect_spin(
        capture, 120.0, 5.0, expected_spin_rpm=8200.0
    )

    assert unmarked.spin_rpm == pytest.approx(8400, rel=0.02)
    assert marked.spin_rpm == pytest.approx(4200, rel=0.02)
    assert marked.method == "envelope_fft+marker_rct"


def test_marker_then_octave_check_walk_a_4x_pick_down_to_1x():
    """4x -> 2x by the marker rule, then 2x -> 1x by the octave check."""
    capture = _am_capture({2820: 0.018, 5640: 0.03, 11280: 0.05}, ball_speed_mph=140.0)

    marker_only = RollingBufferProcessor(ball_marker="dot").detect_spin(
        capture, 140.0, 5.0, expected_spin_rpm=2700.0
    )
    marker_and_octave = RollingBufferProcessor(ball_marker="dot", spin_octave_check=True)
    both = marker_and_octave.detect_spin(capture, 140.0, 5.0, expected_spin_rpm=2700.0)

    assert marker_only.peak_freq_hz * 60 == pytest.approx(5640, rel=0.03)
    assert marker_only.method == "envelope_fft+marker_dot"
    assert both.peak_freq_hz * 60 == pytest.approx(2820, rel=0.03)
    assert both.method == "envelope_fft+marker_dot+octave_halved"
    assert both.octave_corrected


def _range_processor(**kwargs) -> RollingBufferProcessor:
    return RollingBufferProcessor(spin_octave_check=True, spin_octave_prior="range", **kwargs)


@pytest.mark.parametrize(
    ("tones", "ball_speed_mph", "club", "true_rpm", "optimal_correction"),
    [
        pytest.param(
            {2500: 0.04, 5000: 0.05}, 150.0, ClubType.DRIVER, 5000, "halved", id="driver-5000"
        ),
        pytest.param(
            {4200: 0.05, 8400: 0.03}, 100.0, ClubType.IRON_7, 4200, "doubled", id="7-iron-4200"
        ),
    ],
)
def test_range_prior_keeps_real_spin_the_optimal_prior_octave_corrects(
    tones, ball_speed_mph, club, true_rpm, optimal_correction
):
    capture = _am_capture(tones, ball_speed_mph=ball_speed_mph, fade_hz=48.0, fade_depth=0.15)
    prior = get_optimal_spin_for_ball_speed(ball_speed_mph, club)

    optimal = RollingBufferProcessor(spin_octave_check=True).detect_spin_multitaper(
        capture, ball_speed_mph, 5.0, expected_spin_rpm=prior
    )
    ranged = _range_processor().detect_spin_multitaper(
        capture,
        ball_speed_mph,
        5.0,
        expected_spin_rpm=prior,
        plausible_spin_rpm=get_plausible_spin_rpm(club),
    )

    assert optimal.method == f"multitaper_ungated+octave_{optimal_correction}"
    assert optimal.spin_rpm != pytest.approx(true_rpm, rel=0.05)
    assert ranged.spin_rpm == pytest.approx(true_rpm, rel=0.02)
    assert ranged.method == "multitaper_ungated"
    assert not ranged.octave_corrected


@pytest.mark.parametrize(
    ("lines", "plausible_spin_rpm", "corrected_rpm", "correction"),
    [
        pytest.param({4000: 0.8, 8000: 1.0}, (3000, 7000), 4000, "halved", id="halved-at-0.8"),
        pytest.param({4000: 1.0, 8000: 0.9}, (6000, 10000), 8000, "doubled", id="doubled"),
        pytest.param({4000: 0.75, 8000: 1.0}, (3000, 7000), 8000, None, id="alternate-below-0.8"),
        pytest.param({4000: 0.9, 8000: 1.0}, (5000, 9000), 8000, None, id="pick-inside-range"),
        pytest.param(
            {4000: 0.9, 8000: 1.0}, (4500, 7500), 8000, None, id="alternate-outside-range"
        ),
        pytest.param({4000: 0.9, 8000: 1.0}, None, 8000, None, id="no-range"),
    ],
)
def test_range_prior_octave_pick_on_line_spectra(
    lines, plausible_spin_rpm, corrected_rpm, correction
):
    magnitude, freqs = _line_spectrum(lines)

    idx, applied = _range_processor()._octave_corrected_pick(
        magnitude, freqs, int(np.argmax(magnitude)), 2700.0, plausible_spin_rpm
    )

    assert idx == _line_index(freqs, corrected_rpm)
    assert applied == correction


def test_range_prior_with_marker_halves_but_never_doubles():
    marked = _range_processor(ball_marker="dot")

    magnitude, freqs = _line_spectrum({4000: 1.0, 8000: 0.9})
    pick = int(np.argmax(magnitude))
    assert marked._octave_corrected_pick(magnitude, freqs, pick, None, (6000, 10000)) == (
        pick,
        None,
    )

    magnitude, freqs = _line_spectrum({4000: 0.9, 8000: 1.0})
    idx, applied = marked._octave_corrected_pick(
        magnitude, freqs, int(np.argmax(magnitude)), None, (3000, 7000)
    )
    assert (idx, applied) == (_line_index(freqs, 4000), "halved")


def test_range_prior_caps_an_octave_corrected_envelope_pick_to_low():
    capture = _am_capture({4900: 0.045, 9800: 0.05})
    plausible = get_plausible_spin_rpm(ClubType.IRON_7)

    uncorrected = RollingBufferProcessor().detect_spin(capture, 120.0, 5.0)
    lone_line = RollingBufferProcessor().detect_spin(_am_capture({4900: 0.045}), 120.0, 5.0)
    ranged = _range_processor().detect_spin(capture, 120.0, 5.0, plausible_spin_rpm=plausible)

    assert (uncorrected.spin_rpm, uncorrected.quality) == (pytest.approx(9800, rel=0.02), "high")
    assert lone_line.quality == "high"
    assert ranged.spin_rpm == pytest.approx(lone_line.spin_rpm, rel=0.01)
    assert ranged.method == "envelope_fft+octave_halved"
    assert ranged.quality == "low"
    assert ranged.confidence <= 0.5
    assert not ranged.is_reliable


def test_range_prior_caps_an_octave_corrected_multitaper_pick():
    capture = _am_capture({4200: 0.045, 8400: 0.05}, fade_hz=48.0, fade_depth=0.15)

    ranged = _range_processor().detect_spin_multitaper(
        capture, 120.0, 5.0, plausible_spin_rpm=(3000.0, 7000.0)
    )

    assert ranged.spin_rpm == pytest.approx(4200, rel=0.02)
    assert ranged.octave_corrected
    assert ranged.quality in ("low", "experimental")
    assert ranged.confidence <= 0.5


@pytest.mark.parametrize(
    ("prior_mode", "expected_range"),
    [("optimal", None), ("range", get_plausible_spin_rpm(ClubType.IRON_7))],
)
def test_process_capture_passes_the_club_range_only_in_range_mode(
    monkeypatch, prior_mode, expected_range
):
    processor = RollingBufferProcessor(spin_octave_check=True, spin_octave_prior=prior_mode)
    received = {}
    real_detector = processor.detect_spin_multitaper

    def spy(*args, **kwargs):
        received.update(kwargs)
        return real_detector(*args, **kwargs)

    monkeypatch.setattr(processor, "detect_spin_multitaper", spy)
    i_samples, q_samples = synth_capture(
        6500, ball_speed_mph=120.0, amplitude=400.0, noise_rms=10.0, seed=4
    )
    processor.process_capture(
        IQCapture(0.0, 0.068, i_samples, q_samples), club_type=ClubType.IRON_7
    )

    assert received["plausible_spin_rpm"] == expected_range


def test_invalid_spin_octave_prior_is_rejected():
    with pytest.raises(ValueError, match="spin_octave_prior"):
        RollingBufferProcessor(spin_octave_prior="tour")


def test_spin_method_name_tags():
    assert spin_method_name("multitaper_ungated") == "multitaper_ungated"
    assert spin_method_name("envelope_fft", "none", None) == "envelope_fft"
    assert (
        spin_method_name("envelope_fft", "rct", "halved") == "envelope_fft+marker_rct+octave_halved"
    )


class TestServerSpinOptions:
    """CLI flags reach the rolling-buffer monitor and the session metadata."""

    # main() assigns these from argparse; monkeypatch restores them.
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
        "spin_runtime_config",
        "sim_connectors",
    )

    def _run_main(self, monkeypatch, extra_argv):
        for name in self.MAIN_GLOBALS:
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--no-logging", *extra_argv])
        received = {}
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kwargs: None)
        monkeypatch.setattr(
            server_module, "start_monitor", lambda **kwargs: received.update(kwargs)
        )
        monkeypatch.setattr(server_module.socketio, "run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "_prune_session_logs", lambda *_a, **_k: None)
        server_module.main()
        return received

    @pytest.mark.parametrize(
        ("extra_argv", "marker", "octave", "prior"),
        [
            ([], "none", False, "optimal"),
            (["--ball-marker", "dot"], "dot", False, "optimal"),
            (["--ball-marker", "rct", "--spin-octave-check"], "rct", True, "optimal"),
            (["--spin-octave-check"], "none", True, "optimal"),
            (["--spin-octave-check", "--spin-octave-prior", "range"], "none", True, "range"),
        ],
    )
    def test_flags_reach_the_monitor_and_session_metadata(
        self, monkeypatch, extra_argv, marker, octave, prior
    ):
        received = self._run_main(monkeypatch, extra_argv)

        assert received["ball_marker"] == marker
        assert received["spin_octave_check"] is octave
        assert received["spin_octave_prior"] == prior
        assert server_module._session_start_config()["spin"] == {
            "ball_marker": marker,
            "octave_check": octave,
            "octave_prior": prior,
            "cap_prior": False,
            "ball_speed_magnitude_gate": False,
        }

    @pytest.mark.parametrize("flags_on", [False, True])
    def test_prior_cap_and_magnitude_gate_flags_reach_the_monitor(self, monkeypatch, flags_on):
        argv = ["--cap-spin-prior", "--ball-speed-magnitude-gate"] if flags_on else []
        received = self._run_main(monkeypatch, argv)
        assert received["cap_spin_prior"] is flags_on
        assert received["ball_speed_magnitude_gate"] is flags_on
        spin = server_module._session_start_config()["spin"]
        assert spin["cap_prior"] is flags_on
        assert spin["ball_speed_magnitude_gate"] is flags_on

    def test_unknown_spin_octave_prior_is_a_usage_error(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, ["--spin-octave-prior", "tour"])

    def test_unknown_ball_marker_is_a_usage_error(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, ["--ball-marker", "foil"])

    @pytest.mark.parametrize(
        ("marker", "octave", "prior"), [("none", False, "optimal"), ("dot", True, "range")]
    )
    def test_start_monitor_builds_the_monitor_with_the_options(
        self, monkeypatch, marker, octave, prior
    ):
        built = {}

        class FakeMonitor:
            def __init__(self, **kwargs):
                built.update(kwargs)

            def connect(self):
                return True

            def start(self, **_kwargs):
                return None

        for name in ("mock_mode", "mock_swing_speed_mode", "debug_mode"):
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(rolling_buffer_package, "RollingBufferMonitor", FakeMonitor)

        server_module.start_monitor(
            port=None, ball_marker=marker, spin_octave_check=octave, spin_octave_prior=prior
        )

        assert built["ball_marker"] == marker
        assert built["spin_octave_check"] is octave
        assert built["spin_octave_prior"] == prior

    def test_monitor_passes_the_options_to_its_processor(self):
        monitor = RollingBufferMonitor(
            port=None,
            trigger_type="sound",
            ball_marker="rct",
            spin_octave_check=True,
            spin_octave_prior="range",
        )

        assert monitor.processor.ball_marker == "rct"
        assert monitor.processor.spin_octave_check is True
        assert monitor.processor.spin_octave_prior == "range"
