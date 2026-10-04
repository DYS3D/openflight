"""Wedge matrix: the kiosk tags new shots with a swing length."""

from datetime import datetime

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def _shot() -> Shot:
    return Shot(ball_speed_mph=70.0, timestamp=datetime.now(), club=ClubType.GW)


def test_untagged_by_default():
    assert server_module.current_swing_length is None
    assert _shot().to_dict()["swing_length"] is None


def test_set_swing_length_tags_and_clears(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_: emitted.append((event, payload)),
    )
    monkeypatch.setattr(server_module, "current_swing_length", None)

    server_module.handle_set_swing_length({"swing_length": "3/4"})
    assert server_module.current_swing_length == "3/4"
    assert emitted[-1] == ("swing_length_changed", {"swing_length": "3/4"})

    server_module.handle_set_swing_length({"swing_length": None})
    assert server_module.current_swing_length is None


def test_unknown_swing_length_is_refused(monkeypatch):
    replies = []
    monkeypatch.setattr(server_module, "_reply", lambda event, payload: replies.append(event))
    monkeypatch.setattr(server_module, "current_swing_length", "full")

    server_module.handle_set_swing_length({"swing_length": "quarter"})

    assert server_module.current_swing_length == "full"
    assert replies == ["swing_length_error"]
