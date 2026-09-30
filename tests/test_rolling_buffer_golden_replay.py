"""Golden replay: recorded range-session I/Q must reproduce the logged ball speeds.

The rolling-buffer server logs the raw capture (``rolling_buffer_capture``) and
then the shot it emitted (``shot_detected``). The comparison target is the
pre-correction speed: when the opt-in ball-speed cosine correction is enabled the
server logs the processor output as ``ball_speed_raw_mph``; this session ran
without it (no ``ball_speed_raw_mph`` field), so ``ball_speed_mph`` is the
unmodified ``process_capture`` result.

Radar timing flags (``--fast-clock-sync``, ``--rearm-after-handoff``) only
change serial timing and the re-arm order; ``process_capture`` never sees
them, so offline replay provably cannot be affected. The replay is
parametrized over both states anyway, and a second test drives each recorded
dump through the real ``SoundTrigger`` path under both configs.
"""

import json
from pathlib import Path

import pytest

from openflight.radar_timing import ActiveRadarTiming, RadarTimingConfig
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger
from openflight.rolling_buffer.types import IQCapture

SESSION_LOG = Path(__file__).parent.parent / "session_logs" / "session_20260501_180406_range.jsonl"
BALL_SPEED_TOLERANCE_MPH = 0.5

TIMING_CONFIGS = {
    "slow-defaults": RadarTimingConfig(),
    "fast-flags": RadarTimingConfig(fast_clock_sync=True, rearm_after_handoff=True),
}


class _RecordedDumpRadar:
    """Replays one recorded dump and records the trigger's serial calls."""

    def __init__(self, capture: dict):
        self.response = "\n".join(
            [
                json.dumps({"sample_time": str(capture["sample_time"])}),
                json.dumps({"trigger_time": str(capture["trigger_time"])}),
                json.dumps({"I": capture["i_samples"]}),
                json.dumps({"Q": capture["q_samples"]}),
            ]
        )
        self.calls = []
        self.last_clock_sync = None
        self.last_hardware_trigger_first_byte_timestamp = None

    def wait_for_hardware_trigger(self, timeout, cancel_event=None, on_first_byte=None):
        self.calls.append("wait")
        return self.response

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.calls.append("rearm")

    def read_clock_sync(self, samples=7, store=True, **kwargs):
        self.calls.append("clock_sync")
        return {"clock_sync_method": "no_valid_reads", "valid_samples": 0}


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


def _logged_processor_speed(shot_number: int) -> float:
    shot = SHOTS[shot_number]
    return shot.get("ball_speed_raw_mph", shot["ball_speed_mph"])


@pytest.mark.parametrize("timing_name", sorted(TIMING_CONFIGS))
@pytest.mark.parametrize("shot_number", sorted(CAPTURES))
def test_replayed_ball_speed_matches_logged_shot(shot_number, timing_name):
    # Offline replay never consults the timing holder; the parameter only
    # documents that both flag states are covered.
    assert timing_name in TIMING_CONFIGS
    capture = CAPTURES[shot_number]
    result = RollingBufferProcessor().process_capture(
        IQCapture(
            sample_time=capture["sample_time"],
            trigger_time=capture["trigger_time"],
            i_samples=capture["i_samples"],
            q_samples=capture["q_samples"],
        )
    )

    assert result is not None
    assert result.ball_speed_mph == pytest.approx(
        _logged_processor_speed(shot_number), abs=BALL_SPEED_TOLERANCE_MPH
    )


@pytest.mark.parametrize("timing_name", sorted(TIMING_CONFIGS))
@pytest.mark.parametrize("shot_number", sorted(CAPTURES))
def test_sound_trigger_path_reproduces_logged_shot_under_both_timings(shot_number, timing_name):
    """The trigger accepts each recorded dump and only the re-arm order changes."""
    timing = ActiveRadarTiming(TIMING_CONFIGS[timing_name])
    radar = _RecordedDumpRadar(CAPTURES[shot_number])
    trigger = SoundTrigger(timing=timing)
    processor = RollingBufferProcessor()

    capture = trigger.wait_for_trigger(radar, processor, timeout=1.0)
    assert capture is not None

    if timing.active.rearm_after_handoff:
        assert radar.calls == ["wait", "clock_sync"]
        assert trigger.finish_deferred_rearm(radar) is True
    assert radar.calls == ["wait", "clock_sync", "rearm"]

    result = processor.process_capture(capture)
    assert result is not None
    assert result.ball_speed_mph == pytest.approx(
        _logged_processor_speed(shot_number), abs=BALL_SPEED_TOLERANCE_MPH
    )
