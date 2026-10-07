"""Club speed is read just before the ball first appears, not before its strongest echo."""

import json
from pathlib import Path

import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer import IQCapture, RollingBufferProcessor
from openflight.rolling_buffer.types import SpeedReading, SpeedTimeline

# 8-iron shots hit on 2026-10-05 with a SkyTrak+ alongside: shot -> SkyTrak club speed.
RCT_SAMPLE = (
    Path(__file__).parent.parent / "session_logs" / "session_20261005_rct_8iron_sample.jsonl"
)
SKYTRAK_CLUB_MPH = {7: 71.0, 14: 72.0}
STEP_MS = 1.0667


def _reading(speed_mph: float, timestamp_ms: float, magnitude: float = 25.0) -> SpeedReading:
    return SpeedReading(speed_mph, magnitude, timestamp_ms, "outbound")


def _shot_timeline(*, club_mph: float, ball_mph: float, strongest_ball_after_ms: float):
    """Club plateau, then a ball that first reads ~10% low and peaks in strength later."""
    impact_ms = 40.0
    readings = []
    time_ms = impact_ms - 25.0
    while time_ms < impact_ms:
        ramp = min(1.0, (time_ms - (impact_ms - 25.0)) / 12.0)
        readings.append(_reading(club_mph * (0.8 + 0.2 * ramp), time_ms, magnitude=60.0))
        time_ms += STEP_MS
    ball_ms = impact_ms
    strongest_ms = impact_ms + strongest_ball_after_ms
    while ball_ms <= strongest_ms + 5.0:
        settle = min(1.0, (ball_ms - impact_ms) / 20.0)
        strength = 300.0 if abs(ball_ms - strongest_ms) < STEP_MS / 2 else 20.0
        readings.append(_reading(ball_mph * (0.90 + 0.10 * settle), ball_ms, magnitude=strength))
        ball_ms += STEP_MS
    strongest = max(readings, key=lambda reading: reading.magnitude)
    return SpeedTimeline(readings=readings, sample_rate_hz=937.5), strongest.timestamp_ms


@pytest.mark.parametrize("strongest_ball_after_ms", [5.0, 20.0, 35.0])
def test_club_speed_does_not_depend_on_when_the_ball_echo_is_strongest(strongest_ball_after_ms):
    timeline, ball_timestamp_ms = _shot_timeline(
        club_mph=68.0, ball_mph=98.0, strongest_ball_after_ms=strongest_ball_after_ms
    )

    club_mph, club_timestamp_ms = RollingBufferProcessor().find_club_speed(
        timeline, ball_speed_mph=98.0, ball_timestamp_ms=ball_timestamp_ms
    )

    assert club_mph == pytest.approx(68.0, abs=1.0), "the early ball (~88 mph) is not the club"
    assert club_timestamp_ms < 40.0


@pytest.mark.parametrize("shot_number", sorted(SKYTRAK_CLUB_MPH))
def test_recorded_8_iron_club_speed_is_near_skytrak(shot_number):
    captures = {
        row["shot_number"]: row
        for row in map(json.loads, RCT_SAMPLE.read_text(encoding="utf-8").splitlines())
    }
    row = captures[shot_number]
    capture = IQCapture(
        sample_time=0, trigger_time=0, i_samples=row["i_samples"], q_samples=row["q_samples"]
    )

    processed = RollingBufferProcessor().process_capture(capture, club_type=ClubType.IRON_8)

    # The radar reads this golfer's 8-iron ~5-8% under SkyTrak; shot 14 used to read 95 mph.
    assert processed.club_speed_mph == pytest.approx(SKYTRAK_CLUB_MPH[shot_number], rel=0.10)
