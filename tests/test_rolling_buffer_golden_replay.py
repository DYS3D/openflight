"""Golden replay: recorded range-session I/Q must reproduce the logged ball speeds.

The rolling-buffer server logs the raw capture (``rolling_buffer_capture``) and
then the shot it emitted (``shot_detected``). The comparison target is the
pre-correction speed: when the opt-in ball-speed cosine correction is enabled the
server logs the processor output as ``ball_speed_raw_mph``; this session ran
without it (no ``ball_speed_raw_mph`` field), so ``ball_speed_mph`` is the
unmodified ``process_capture`` result.
"""

import json
from pathlib import Path

import pytest

from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

SESSION_LOG = Path(__file__).parent.parent / "session_logs" / "session_20260501_180406_range.jsonl"
BALL_SPEED_TOLERANCE_MPH = 0.5


def _load_session(path: Path) -> tuple[dict[int, dict], dict[int, dict]]:
    captures: dict[int, dict] = {}
    shots: dict[int, dict] = {}
    with path.open() as f:
        for line in f:
            entry = json.loads(line)
            if entry["type"] == "rolling_buffer_capture":
                captures[entry["shot_number"]] = entry
            elif entry["type"] == "shot_detected":
                shots[entry["shot_number"]] = entry
    return captures, shots


CAPTURES, SHOTS = _load_session(SESSION_LOG)


def test_session_pairs_every_capture_with_a_shot():
    assert len(CAPTURES) == 9
    assert sorted(CAPTURES) == sorted(SHOTS)


@pytest.mark.parametrize("shot_number", sorted(CAPTURES))
def test_replayed_ball_speed_matches_logged_shot(shot_number):
    capture = CAPTURES[shot_number]
    result = RollingBufferProcessor().process_capture(
        IQCapture(
            sample_time=capture["sample_time"],
            trigger_time=capture["trigger_time"],
            i_samples=capture["i_samples"],
            q_samples=capture["q_samples"],
        )
    )

    shot = SHOTS[shot_number]
    logged_processor_speed = shot.get("ball_speed_raw_mph", shot["ball_speed_mph"])

    assert result is not None
    assert result.ball_speed_mph == pytest.approx(
        logged_processor_speed, abs=BALL_SPEED_TOLERANCE_MPH
    )
