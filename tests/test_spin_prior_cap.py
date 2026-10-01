"""Opt-in cap on the spin prior at the club's plausible max and the detector ceiling."""

import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer import (
    IQCapture,
    RollingBufferMonitor,
    RollingBufferProcessor,
    get_optimal_spin_for_ball_speed,
)
from tests.spin_synth import synth_capture


def _prior_seen_by_detector(processor, club):
    seen = []
    detector = processor.detect_spin_multitaper

    def recording_detector(*args, **kwargs):
        seen.append(kwargs["expected_spin_rpm"])
        return detector(*args, **kwargs)

    processor.detect_spin_multitaper = recording_detector
    i_samples, q_samples = synth_capture(rpm=8000, ball_speed_mph=82.0, amplitude=400.0)
    capture = IQCapture(sample_time=0.0, trigger_time=0.0, i_samples=i_samples, q_samples=q_samples)
    processed = processor.process_capture(
        capture,
        expected_spin_for_ball_speed=lambda mph: get_optimal_spin_for_ball_speed(mph, club),
        club_type=club,
    )
    assert processed is not None
    return seen[0]


@pytest.mark.parametrize(
    ("club", "uncapped", "capped"),
    [(ClubType.PW, 11520, 11000), (ClubType.SW, 13760, 12000), (ClubType.LW, 14720, 12000)],
)
def test_wedge_prior_is_capped_only_when_enabled(club, uncapped, capped):
    assert _prior_seen_by_detector(RollingBufferProcessor(), club) == pytest.approx(uncapped)
    assert _prior_seen_by_detector(
        RollingBufferProcessor(cap_spin_prior=True), club
    ) == pytest.approx(capped)


def test_prior_below_the_caps_is_unchanged():
    prior = _prior_seen_by_detector(RollingBufferProcessor(cap_spin_prior=True), ClubType.DRIVER)

    assert prior == pytest.approx(get_optimal_spin_for_ball_speed(82.0, ClubType.DRIVER))


def test_monitor_plumbs_cap_spin_prior():
    assert RollingBufferMonitor(port=None).processor.cap_spin_prior is False
    assert RollingBufferMonitor(port=None, cap_spin_prior=True).processor.cap_spin_prior is True
