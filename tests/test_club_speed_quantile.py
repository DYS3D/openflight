"""--club-speed-quantile: which quantile of the club's pre-impact plateau is reported."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

SESSION = Path(__file__).parent.parent / "session_logs" / "session_20260506_152406_range.jsonl"


def _captures():
    for line in SESSION.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["type"] == "rolling_buffer_capture":
            yield IQCapture(
                sample_time=entry["sample_time"],
                trigger_time=entry["trigger_time"],
                i_samples=entry["i_samples"],
                q_samples=entry["q_samples"],
            )


def test_default_quantile_is_unchanged():
    assert (
        RollingBufferProcessor().club_plateau_quantile
        == RollingBufferProcessor.CLUB_PLATEAU_QUANTILE
        == 0.70
    )


def test_higher_quantile_reads_club_speed_no_lower_and_ball_speed_unchanged():
    default = RollingBufferProcessor(sample_rate=30000)
    higher = RollingBufferProcessor(sample_rate=30000, club_plateau_quantile=0.85)
    raised = 0
    for capture in list(_captures())[:8]:
        base = default.process_capture(capture, club_type=ClubType.DRIVER)
        tuned = higher.process_capture(capture, club_type=ClubType.DRIVER)
        assert tuned.ball_speed_mph == base.ball_speed_mph
        if base.club_speed_mph is not None and tuned.club_speed_mph is not None:
            assert tuned.club_speed_mph >= base.club_speed_mph
            raised += tuned.club_speed_mph > base.club_speed_mph
    assert raised > 0


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_server_rejects_quantiles_outside_zero_to_one(monkeypatch, value):
    from openflight import server

    monkeypatch.setattr("sys.argv", ["openflight-server", "--mock", "--club-speed-quantile", value])
    with pytest.raises(SystemExit):
        server.main()
