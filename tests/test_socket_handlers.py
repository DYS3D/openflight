"""Every @socketio.on handler survives valid, empty and non-dict payloads.

Uses the Flask-SocketIO test client so events travel through the real
dispatch path (argument binding included), not direct function calls.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from openflight import server as server_module

SERVER_SOURCE = Path(server_module.__file__).read_text(encoding="utf-8")

# Payloads a buggy or hostile client might send. ``NO_ARGS`` means the event
# is emitted with no payload at all.
NO_ARGS = object()
ODD_PAYLOADS = [NO_ARGS, {}, None, "string", 42, [1, 2], {"unexpected": True}]

# A well-formed payload per event (None = the event takes no payload).
VALID_PAYLOADS = {
    "get_camera_capture_settings": None,
    "set_camera_capture_settings": {"alignment_x_pct": 50.0, "alignment_y_pct": 50.0},
    "get_trigger_status": None,
    "set_club": {"club": "7-iron"},
    "get_profiles": None,
    "set_active_profile": {"profile_id": "missing"},
    "add_profile": {"name": "Handler Test"},
    "rename_profile": {"profile_id": "missing", "name": "Renamed"},
    "remove_profile": {"profile_id": "missing"},
    "set_training_implement": {"implement": "driver"},
    "clear_session": {"profile_id": "missing"},
    "upload_cloud": None,
    "get_session": None,
    "delete_shot": {"timestamp": "2026-01-01T00:00:00"},
    "simulate_shot": None,
    "toggle_debug": {"enabled": False},
    "get_debug_status": None,
    "get_radar_config": None,
    "set_radar_config": {"min_speed": 20},
    "shutdown": None,
    "get_update_status": None,
    "check_for_updates": None,
    "apply_update": None,
}

# Events whose real side effects must be neutralised for a unit test.
SIDE_EFFECT_EVENTS = {"shutdown", "upload_cloud", "toggle_debug"}


def registered_events() -> list[str]:
    """Event names from the source, so a new handler cannot go untested."""
    return re.findall(r'@socketio\.on\("([a-z_]+)"\)', SERVER_SOURCE)


def test_every_registered_event_has_a_valid_payload_case():
    events = set(registered_events()) - {"connect", "disconnect"}
    assert events == set(VALID_PAYLOADS), "add new socket events to VALID_PAYLOADS"


def test_every_handler_accepts_any_argument_shape():
    """Flask-SocketIO binds the payload positionally; signatures must allow 0 or 1."""
    for event in registered_events():
        handler = server_module.socketio.server.handlers["/"][event]
        target = inspect.unwrap(handler)
        params = list(inspect.signature(target).parameters.values())
        assert params, f"{event}: handler must accept a payload argument"
        first = params[0]
        assert first.kind is inspect.Parameter.VAR_POSITIONAL or first.default is not first.empty, (
            f"{event}: payload argument must be optional or *args"
        )


@pytest.fixture
def quiet_server(monkeypatch, tmp_path):
    """Server with no hardware, no session log, and side effects stubbed."""
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "mock_mode", False)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "debug_mode", False)
    monkeypatch.setattr(server_module, "DEBUG_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        server_module, "profile_store", server_module.ProfileStore(tmp_path / "p.json")
    )
    started = []
    monkeypatch.setattr(
        server_module.threading,
        "Thread",
        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw)})(),
    )
    yield server_module
    server_module.stop_debug_logging()


@pytest.fixture
def client(quiet_server):
    socket_client = quiet_server.socketio.test_client(quiet_server.app)
    socket_client.get_received()
    yield socket_client
    if socket_client.is_connected():
        socket_client.disconnect()


def _emit(client, event, payload):
    if payload is NO_ARGS:
        client.emit(event)
    else:
        client.emit(event, payload)


@pytest.mark.parametrize("event", sorted(VALID_PAYLOADS))
@pytest.mark.parametrize(
    "payload", ODD_PAYLOADS, ids=lambda p: "no-args" if p is NO_ARGS else repr(p)
)
def test_handlers_never_raise_on_odd_payloads(client, event, payload):
    _emit(client, event, payload)
    assert client.is_connected()


@pytest.mark.parametrize("event", sorted(VALID_PAYLOADS))
def test_handlers_accept_their_valid_payload(client, event):
    payload = VALID_PAYLOADS[event]
    _emit(client, event, NO_ARGS if payload is None else payload)
    assert client.is_connected()


class TestObservableReplies:
    """A few events must answer the caller even with an empty payload."""

    def _names(self, client):
        return [r["name"] for r in client.get_received()]

    def test_get_events_reply(self, client):
        for event, reply in [
            ("get_radar_config", "radar_config"),
            ("get_debug_status", "debug_status"),
            ("get_trigger_status", "trigger_status"),
            ("get_profiles", "profiles"),
            ("get_camera_capture_settings", "camera_capture_settings"),
        ]:
            client.emit(event, {})
            assert reply in self._names(client), event

    def test_set_club_with_empty_payload_falls_back_to_driver(self, client):
        client.emit("set_club", {})
        received = client.get_received()
        assert [r["args"][0] for r in received if r["name"] == "club_changed"] == [
            {"club": "driver"}
        ]

    def test_set_radar_config_without_radar_reports_error(self, client):
        client.emit("set_radar_config", "garbage")
        assert "radar_config_error" in self._names(client)

    def test_delete_shot_with_non_dict_reports_not_found(self, client):
        client.emit("delete_shot", 42)
        assert "delete_shot_error" in self._names(client)

    def test_camera_settings_without_runtime_reports_error(self, client):
        client.emit("set_camera_capture_settings", [])
        assert "camera_capture_settings_error" in self._names(client)

    def test_training_implement_unknown_is_rejected(self, client):
        client.emit("set_training_implement", {"implement": "not-a-thing"})
        assert "training_implement_error" in self._names(client)
