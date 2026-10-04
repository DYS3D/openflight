"""Read-only session log API for a home dashboard (``--session-log-api``)."""

from __future__ import annotations

import argparse
import json

from flask import Flask

from openflight import server as server_module
from openflight.session_log_api import (
    add_session_log_api_args,
    create_session_log_blueprint,
    list_session_logs,
    resolve_session_log,
)
from openflight.session_logger import SessionLogger


def _write(path, text="{}\n"):
    path.write_text(text, encoding="utf-8")
    return path


def test_flag_is_off_by_default():
    parser = argparse.ArgumentParser()
    add_session_log_api_args(parser)
    assert parser.parse_args([]).session_log_api is False
    assert parser.parse_args(["--session-log-api"]).session_log_api is True


def test_server_does_not_expose_logs_without_the_flag():
    rules = {rule.rule for rule in server_module.app.url_map.iter_rules()}
    assert "/api/session-logs" not in rules


def test_lists_only_session_logs(tmp_path):
    _write(tmp_path / "session_20261003_190737_range.jsonl")
    _write(tmp_path / "radar_raw_20261003_190737.log")
    (tmp_path / "range").mkdir()

    names = [entry["name"] for entry in list_session_logs(tmp_path)]

    assert names == ["session_20261003_190737_range.jsonl"]
    assert list_session_logs(tmp_path / "missing") == []


def test_refuses_paths_outside_the_log_dir(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write(tmp_path / "session_outside.jsonl")
    _write(log_dir / "notes.txt")

    assert resolve_session_log(log_dir, "../session_outside.jsonl") is None
    assert resolve_session_log(log_dir, "notes.txt") is None
    assert resolve_session_log(log_dir, "session_missing.jsonl") is None


def test_routes_list_and_serve_a_session(tmp_path):
    _write(tmp_path / "session_20261003_190737_range.jsonl", '{"type": "session_start"}\n')
    app = Flask(__name__)
    app.register_blueprint(create_session_log_blueprint(lambda: tmp_path))
    client = app.test_client()

    listing = client.get("/api/session-logs").get_json()
    assert [entry["name"] for entry in listing["sessions"]] == [
        "session_20261003_190737_range.jsonl"
    ]
    body = client.get("/api/session-logs/session_20261003_190737_range.jsonl")
    assert body.status_code == 200
    assert body.get_data(as_text=True) == '{"type": "session_start"}\n'
    assert client.get("/api/session-logs/radar_raw_x.log").status_code == 404


def test_session_logger_records_deleted_shots(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")

    logger.log_shot_deleted("2026-10-03T19:10:00")

    logger.flush()
    entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
    assert entry["type"] == "shot_deleted"
    assert entry["shot_timestamp"] == "2026-10-03T19:10:00"


def test_delete_shot_handler_logs_the_deletion(monkeypatch):
    deleted = []
    fake_logger = type("L", (), {"log_shot_deleted": lambda self, ts: deleted.append(ts)})()
    monkeypatch.setattr(server_module, "_delete_session_row", lambda timestamp: True)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: fake_logger)
    monkeypatch.setattr(server_module, "_session_state_payload", lambda: {})
    client = server_module.socketio.test_client(server_module.app)
    try:
        client.emit("delete_shot", {"timestamp": "2026-10-03T19:10:00"})
    finally:
        client.disconnect()

    assert deleted == ["2026-10-03T19:10:00"]


def _offload_fixture(tmp_path):
    """A finished session with an IWR dump, a camera shot dir and an outside path."""
    iwr = tmp_path / "iwr6843"
    iwr.mkdir()
    dump = iwr / "iwr6843_0001.l3dump"
    dump.write_bytes(b"x" * 10)
    shot_dir = tmp_path / "range" / "camera" / "camera_0001"
    shot_dir.mkdir(parents=True)
    (shot_dir / "frames.npz").write_bytes(b"y" * 20)
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"z")
    entries = [
        {"type": "session_start"},
        {"type": "iwr6843_capture", "capture_path": str(dump)},
        {"type": "camera_capture", "capture_path": str(shot_dir)},
        {"type": "camera_capture", "capture_path": str(outside)},
        {"type": "camera_capture", "capture_path": None},
    ]
    session = _write(
        tmp_path / "session_20261003_190737_range.jsonl",
        "".join(json.dumps(entry) + "\n" for entry in entries),
    )
    return session, dump, shot_dir, outside


def _offload_client(tmp_path, *, allow=True, active=None):
    app = Flask(__name__)
    app.register_blueprint(
        create_session_log_blueprint(lambda: tmp_path, lambda: active, allow_offload=allow)
    )
    return app.test_client()


def test_offload_flag_is_off_by_default():
    parser = argparse.ArgumentParser()
    add_session_log_api_args(parser)
    assert parser.parse_args([]).session_log_offload is False


def test_offload_routes_are_hidden_without_the_flag(tmp_path):
    session, *_ = _offload_fixture(tmp_path)
    client = _offload_client(tmp_path, allow=False)

    assert client.get(f"/api/session-logs/{session.name}/manifest").status_code == 404
    assert client.delete(f"/api/session-logs/{session.name}", json={}).status_code == 404
    assert session.exists()


def test_manifest_lists_captures_inside_the_log_dir_only(tmp_path):
    session, *_ = _offload_fixture(tmp_path)
    client = _offload_client(tmp_path)

    manifest = client.get(f"/api/session-logs/{session.name}/manifest").get_json()

    assert manifest["files"] == [
        {"path": "iwr6843/iwr6843_0001.l3dump", "size": 10},
        {"path": "range/camera/camera_0001/frames.npz", "size": 20},
    ]
    capture = client.get(f"/api/session-logs/{session.name}/files/iwr6843/iwr6843_0001.l3dump")
    assert capture.get_data() == b"x" * 10
    assert client.get(f"/api/session-logs/{session.name}/files/../outside.bin").status_code == 404


def test_listing_marks_the_session_being_recorded(tmp_path):
    session, *_ = _offload_fixture(tmp_path)
    listing = _offload_client(tmp_path, active=session.name).get("/api/session-logs").get_json()
    assert listing["sessions"][0]["active"] is True
    assert listing["offload"] is True


def test_delete_needs_an_identical_copy_and_spares_the_active_session(tmp_path):
    session, dump, shot_dir, outside = _offload_fixture(tmp_path)
    manifest = (
        _offload_client(tmp_path).get(f"/api/session-logs/{session.name}/manifest").get_json()
    )
    sizes = {entry["path"]: entry["size"] for entry in manifest["files"]}

    active = _offload_client(tmp_path, active=session.name)
    assert (
        active.delete(
            f"/api/session-logs/{session.name}", json={"sha256": manifest["sha256"], "files": sizes}
        ).status_code
        == 409
    )

    client = _offload_client(tmp_path)
    short = dict(sizes, **{"iwr6843/iwr6843_0001.l3dump": 9})
    assert (
        client.delete(
            f"/api/session-logs/{session.name}", json={"sha256": manifest["sha256"], "files": short}
        ).status_code
        == 412
    )
    assert (
        client.delete(
            f"/api/session-logs/{session.name}", json={"sha256": "0" * 64, "files": sizes}
        ).status_code
        == 412
    )
    assert session.exists() and dump.exists()

    response = client.delete(
        f"/api/session-logs/{session.name}", json={"sha256": manifest["sha256"], "files": sizes}
    )

    assert response.status_code == 200
    assert not session.exists() and not dump.exists() and not shot_dir.exists()
    assert outside.exists()


def test_active_session_name_comes_from_the_session_logger(tmp_path, monkeypatch):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    monkeypatch.setattr(server_module, "get_session_logger", lambda: logger)

    assert server_module._active_session_log_name() == logger.session_path.name
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    assert server_module._active_session_log_name() is None
