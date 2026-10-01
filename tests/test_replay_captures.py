"""replay_captures must replay each capture at the sample rate it was logged at."""

import importlib.util
import json
import sys
from pathlib import Path

from openflight.rolling_buffer.processor import RollingBufferProcessor

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "replay_captures.py"
spec = importlib.util.spec_from_file_location("replay_captures", SCRIPT)
replay_captures = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = replay_captures
spec.loader.exec_module(replay_captures)


def _write_session(tmp_path, captures):
    path = tmp_path / "session.jsonl"
    lines = []
    for extra in captures:
        entry = {
            "type": "rolling_buffer_capture",
            "sample_time": 0.0,
            "trigger_time": 0.0,
            "i_samples": [2048] * 4096,
            "q_samples": [2048] * 4096,
        }
        entry.update(extra)
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _replayed_rates(monkeypatch, tmp_path, captures, *cli_args):
    rates = []

    class RecordingProcessor(RollingBufferProcessor):
        def __init__(self, sample_rate=30000, **kwargs):
            super().__init__(sample_rate=sample_rate, **kwargs)
            rates.append(sample_rate)

    monkeypatch.setattr(replay_captures, "RollingBufferProcessor", RecordingProcessor)
    session = _write_session(tmp_path, captures)
    monkeypatch.setattr(sys, "argv", ["replay_captures.py", str(session), "--summary", *cli_args])
    replay_captures.main()
    return rates


def test_uses_logged_rate_and_defaults_old_logs_to_30ksps(monkeypatch, tmp_path):
    rates = _replayed_rates(monkeypatch, tmp_path, [{"sample_rate_hz": 20000}, {}])

    assert sorted(rates) == [20000, 30000]


def test_cli_sample_rate_overrides_logged_rate(monkeypatch, tmp_path):
    rates = _replayed_rates(monkeypatch, tmp_path, [{"sample_rate_hz": 20000}], "--sample-rate", "40")

    assert rates == [40000]
