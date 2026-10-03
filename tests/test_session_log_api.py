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
