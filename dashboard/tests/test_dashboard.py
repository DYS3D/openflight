"""Log parsing, storage, sync and the JSON API of the home dashboard."""

from __future__ import annotations

import json

import pytest

from openflight_dashboard import stats
from openflight_dashboard.app import create_app
from openflight_dashboard.logs import parse_session_log
from openflight_dashboard.store import Store
from openflight_dashboard.sync import sync_once

NAME = "session_20261003_190737_range.jsonl"


def shot(timestamp, *, club="7-iron", ball=110.0, carry=150, smash=1.4, profile="justin", **extra):
    return {
        "type": "shot_detected",
        "timestamp": timestamp,
        "shot_number": 1,
        "club": club,
        "mode": "rolling-buffer",
        "profile_id": profile,
        "profile_name": profile.title(),
        "ball_speed_mph": ball,
        "club_speed_mph": round(ball / smash, 1),
        "smash_factor": smash,
        "estimated_carry_yards": carry,
        "carry_spin_adjusted": None,
        **extra,
    }


def log_text(*entries):
    start = {"type": "session_start", "ts": "2026-10-03T19:07:37"}
    return "\n".join(json.dumps(entry) for entry in (start, *entries)) + "\n"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "db.sqlite3")


def test_parse_drops_deleted_mock_and_swing_speed_shots_and_torn_lines():
    text = (
        log_text(
            shot("2026-10-03T19:10:00"),
            shot("2026-10-03T19:11:00"),
            shot("2026-10-03T19:12:00", mode="mock"),
            shot("2026-10-03T19:13:00", club="Swing Speed"),
            {"type": "shot_deleted", "shot_timestamp": "2026-10-03T19:11:00"},
        )
        + '{"type": "shot_det'
    )

    session = parse_session_log(NAME, text)

    assert session.session_id == "session_20261003_190737_range"
    assert session.started_at == "2026-10-03T19:07:37"
    assert [s.timestamp for s in session.shots] == ["2026-10-03T19:10:00"]


def test_parse_prefers_spin_adjusted_carry():
    session = parse_session_log(NAME, log_text(shot("t1", carry=150, carry_spin_adjusted=158.0)))
    assert session.shots[0].carry == 158.0


def test_parse_falls_back_to_the_file_name_for_the_start_time():
    session = parse_session_log(NAME, json.dumps(shot("t1")) + "\n")
    assert session.started_at == "2026-10-03T19:07:37"


def test_ingest_replaces_a_session_when_the_log_grows(store):
    store.ingest(NAME, log_text(shot("t1")), 100, 1.0)
    store.ingest(NAME, log_text(shot("t1"), shot("t2")), 200, 2.0)

    assert stats.summary(store)["shots"] == 2
    assert store.known_file(NAME) == (200, 2.0)


class FakeSource:
    base_url = "http://pi"

    def __init__(self, logs):
        self.logs = logs
        self.reads = []

    def list_logs(self):
        return [{"name": name, "size": len(text), "mtime": 1.0} for name, text in self.logs.items()]

    def read_log(self, name):
        self.reads.append(name)
        return self.logs[name]


def test_sync_fetches_only_new_or_changed_logs(store):
    source = FakeSource({NAME: log_text(shot("t1"))})

    assert sync_once(store, source) == {"ok": True, "updated": 1, "available": 1}
    assert sync_once(store, source)["updated"] == 0
    source.logs[NAME] = log_text(shot("t1"), shot("t2"))
    assert sync_once(store, source)["updated"] == 1
    assert source.reads == [NAME, NAME]
    assert store.get_state("last_error") is None


def test_sync_failure_is_recorded_and_keeps_existing_data(store):
    store.ingest(NAME, log_text(shot("t1")), 1, 1.0)

    class Offline:
        base_url = "http://pi"

        def list_logs(self):
            raise OSError("no route to host")

    assert sync_once(store, Offline())["ok"] is False
    assert "no route to host" in store.get_state("last_error")
    assert stats.summary(store)["shots"] == 1


def test_gapping_uses_quartiles_and_orders_clubs_by_bag(store):
    carries = [140, 150, 152, 154, 200]
    store.ingest(
        NAME,
        log_text(
            *(shot(f"t{i}", carry=carry) for i, carry in enumerate(carries)),
            shot("d1", club="driver", carry=240),
        ),
        1,
        1.0,
    )

    rows = stats.gapping(store, None, None)

    assert [row["club"] for row in rows] == ["driver", "7-iron"]
    iron = rows[1]
    assert (iron["p25"], iron["median"], iron["p75"]) == (150, 152, 154)
    assert (iron["min"], iron["max"]) == (140, 200)


def test_records_ignore_club_speed_from_impossible_smash(store):
    store.ingest(
        NAME,
        log_text(shot("t1", ball=110, smash=1.4), shot("t2", ball=100, smash=3.8)),
        1,
        1.0,
    )

    record = stats.records(store, None)[0]

    assert record["ball_speed"]["value"] == 110
    assert record["club_speed"]["value"] == round(110 / 1.4, 1)


def test_profile_filter_scopes_every_view(store):
    store.ingest(NAME, log_text(shot("t1"), shot("t2", profile="guest")), 1, 1.0)

    assert stats.sessions(store, "guest")[0]["shots"] == 1
    assert stats.sessions(store, None)[0]["shots"] == 2
    assert {p["id"] for p in stats.profiles(store)} == {"justin", "guest"}


def test_trends_give_one_median_per_session(store):
    store.ingest(
        NAME, log_text(shot("t1", carry=140), shot("t2", carry=150), shot("t3", carry=190)), 1, 1.0
    )

    points = stats.trends(store, None)[0]["points"]

    assert len(points) == 1
    assert points[0]["carry"] == 150
    assert points[0]["shots"] == 3


def test_api_serves_pages_and_reports_missing_sessions(store):
    store.ingest(NAME, log_text(shot("t1")), 1, 1.0)
    client = create_app(store, None, None).test_client()

    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/summary").get_json()["sync_enabled"] is False
    assert client.get("/api/sessions").get_json()[0]["id"] == "session_20261003_190737_range"
    assert (
        client.get("/api/sessions/session_20261003_190737_range").get_json()["shots"][0][
            "club_name"
        ]
        == "7 Iron"
    )
    assert client.get("/api/sessions/missing").status_code == 404
    assert client.post("/api/sync").status_code == 409
