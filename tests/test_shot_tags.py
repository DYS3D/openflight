"""Shot tags: the Shots screen labels a shot after the fact."""

from datetime import datetime

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


class FakeMonitor:
    def __init__(self, shots):
        self._shots = shots

    def get_shots(self):
        return list(self._shots)


class FakeLogger:
    def __init__(self):
        self.tagged = []

    def log_shot_tagged(self, timestamp, tags):
        self.tagged.append((timestamp, tags))


def _setup(monkeypatch):
    shot = Shot(ball_speed_mph=90.0, timestamp=datetime(2026, 10, 4, 12), club=ClubType.IRON_8)
    logger = FakeLogger()
    replies, emitted = [], []
    monkeypatch.setattr(server_module, "monitor", FakeMonitor([shot]))
    monkeypatch.setattr(server_module, "get_session_logger", lambda: logger)
    monkeypatch.setattr(server_module, "_session_state_payload", lambda: {})
    monkeypatch.setattr(server_module, "_reply", lambda event, payload: replies.append(event))
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, *a, **k: emitted.append(event)
    )
    return shot, logger, replies, emitted


def test_shots_start_untagged():
    shot = Shot(ball_speed_mph=90.0, timestamp=datetime.now(), club=ClubType.IRON_8)
    assert shot.to_dict()["tags"] == []


def test_tag_shot_updates_logs_and_refreshes(monkeypatch):
    shot, logger, replies, emitted = _setup(monkeypatch)

    server_module.handle_tag_shot({"timestamp": "2026-10-04T12:00:00", "tags": ["toe", "mishit"]})

    assert shot.tags == ["mishit", "toe"]
    assert logger.tagged == [("2026-10-04T12:00:00", ["mishit", "toe"])]
    assert emitted == ["session_state"]
    assert replies == []


def test_tag_shot_refuses_unknown_tags_and_shots(monkeypatch):
    shot, logger, replies, _ = _setup(monkeypatch)

    server_module.handle_tag_shot({"timestamp": "2026-10-04T12:00:00", "tags": ["shank"]})
    server_module.handle_tag_shot({"timestamp": "2026-10-04T13:00:00", "tags": ["good"]})

    assert shot.tags == []
    assert logger.tagged == []
    assert replies == ["tag_shot_error", "tag_shot_error"]
