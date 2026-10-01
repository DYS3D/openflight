"""Tests for openflight-cloud command orchestration (link/push/status)."""

import json
import os
import time

import pytest

from openflight.cloud import commands, spool
from openflight.cloud.client import LinkPoll, LinkStart, UploadResult
from openflight.cloud.config import CloudConfig


class FakeClient:
    def __init__(self, *, healthy=True, link_start=None, polls=None, uploads=None):
        self._healthy = healthy
        self._link_start = link_start
        self._polls = list(polls or [])
        self._uploads = list(uploads or [])
        self.uploaded = []

    def health(self):
        return self._healthy

    def device_link_start(self, device_name, client_version):
        return self._link_start

    def device_link_poll(self, poll_token):
        return self._polls.pop(0)

    def upload_session(self, session_id, body):
        self.uploaded.append(session_id)
        return self._uploads.pop(0)


def _write_session(tmp_path, name, *entries):
    path = tmp_path / name
    entries = (*entries, {"type": "session_end"})
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def _linked_config():
    return CloudConfig(
        endpoint="https://e.test",
        device_token="of_device_tok",
        device_id="dev-1",
        enabled=True,
    )


class TestPush:
    def test_offline_short_circuits(self, tmp_path):
        _write_session(tmp_path, "session_a.jsonl", {"type": "session_start"})
        client = FakeClient(healthy=False)
        out = []
        result = commands.cmd_push(_linked_config(), tmp_path, client, out=out.append)
        assert result["offline"] is True
        assert client.uploaded == []

    def test_inactive_config_is_noop(self, tmp_path):
        _write_session(tmp_path, "session_a.jsonl", {"type": "session_start"})
        config = CloudConfig(enabled=False)
        client = FakeClient()
        result = commands.cmd_push(config, tmp_path, client, out=lambda _m: None)
        assert result["skipped"] == "inactive"
        assert client.uploaded == []

    def test_success_marks_pushed(self, tmp_path):
        path = _write_session(
            tmp_path,
            "session_a.jsonl",
            {"type": "session_start", "session_uuid": "1f0e9c2a-7b3d-4e5f-8a9b-0c1d2e3f4a5b"},
            {"type": "shot_detected", "ball_speed_mph": 90},
        )
        client = FakeClient(
            uploads=[UploadResult(201, action="success", session_id="x", shot_count=1)]
        )
        result = commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == ["1f0e9c2a-7b3d-4e5f-8a9b-0c1d2e3f4a5b"]
        assert spool.is_pushed(path)
        assert result["uploaded"] == 1

    def test_dry_run_does_not_upload_or_mark(self, tmp_path):
        path = _write_session(
            tmp_path,
            "session_a.jsonl",
            {"type": "session_start", "session_uuid": "u"},
            {"type": "shot_detected", "ball_speed_mph": 90},
            {"type": "rolling_buffer_capture", "i_samples": [1, 2, 3]},
        )
        client = FakeClient()
        out = []
        commands.cmd_push(_linked_config(), tmp_path, client, dry_run=True, out=out.append)
        assert client.uploaded == []
        assert not spool.is_pushed(path)
        printed = "\n".join(out)
        # The privacy answer: shows kept types, hides dropped raw data.
        assert "shot_detected" in printed
        assert "session_start" in printed
        assert "rolling_buffer_capture" not in printed

    def test_relink_stops_and_flags(self, tmp_path):
        _write_session(tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"})
        _write_session(tmp_path, "session_b.jsonl", {"type": "session_start", "session_uuid": "b"})
        client = FakeClient(
            uploads=[UploadResult(401, action="relink", reason="invalid_or_revoked_token")]
        )
        result = commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert result["needs_relink"] is True
        # Stops after the first 401 — does not attempt the second session.
        assert len(client.uploaded) == 1

    def test_park_on_422(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        client = FakeClient(uploads=[UploadResult(422, action="park", reason="invalid_gzip")])
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert spool.is_parked(path)

    def test_quota_sets_cooldown_not_park(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        client = FakeClient(uploads=[UploadResult(402, action="quota", reason="quota_exceeded")])
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert not spool.is_parked(path)
        assert spool.in_cooldown(path)

    def test_5xx_records_failure_and_leaves_pending(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        client = FakeClient(uploads=[UploadResult(503, action="retry")])
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert spool.read_attempts(path) == 1
        assert not spool.is_pushed(path)
        assert not spool.is_parked(path)

    def test_session_still_being_written_is_not_pushed(self, tmp_path):
        path = tmp_path / "session_a.jsonl"
        path.write_text(
            json.dumps({"type": "session_start", "session_uuid": "a"})
            + "\n"
            + json.dumps({"type": "shot_detected", "ball_speed_mph": 90})
            + "\n"
        )
        client = FakeClient(uploads=[UploadResult(201, action="success", shot_count=1)])
        result = commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == []
        assert not spool.is_pushed(path)
        assert result["deferred"] == 1

    def test_crashed_session_without_session_end_is_pushed_once_stale(self, tmp_path):
        path = tmp_path / "session_a.jsonl"
        path.write_text(json.dumps({"type": "session_start", "session_uuid": "a"}) + "\n")
        stale = time.time() - spool.IN_PROGRESS_GRACE_S - 60
        os.utime(path, (stale, stale))
        client = FakeClient(uploads=[UploadResult(201, action="success", shot_count=0)])
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == ["a"]
        assert spool.is_pushed(path)

    def test_oversize_body_parks(self, tmp_path, monkeypatch):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "shot_detected", "ball_speed_mph": 90}
        )
        from openflight.cloud import filtering

        monkeypatch.setattr(filtering, "MAX_GZIP_BYTES", 1)
        client = FakeClient(uploads=[UploadResult(201, action="success")])
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == []
        assert spool.is_parked(path)

    def test_skips_sessions_in_cooldown(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.record_cooldown(path, "quota_exceeded", seconds=spool.QUOTA_COOLDOWN_S)
        client = FakeClient(uploads=[UploadResult(201, action="success")])
        result = commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == []
        assert result["deferred"] == 1


class TestPushRetry:
    def test_retry_all_reuploads_parked_session(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.mark_parked(path, reason="max_attempts", attempts=20, last_error="503")
        client = FakeClient(uploads=[UploadResult(201, action="success", shot_count=1)])
        result = commands.cmd_push(
            _linked_config(), tmp_path, client, retry=True, out=lambda _m: None
        )
        assert client.uploaded == ["a"]
        assert spool.is_pushed(path)
        assert result["uploaded"] == 1

    def test_retry_all_reuploads_cooled_down_session(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.record_cooldown(path, "quota_exceeded", seconds=spool.QUOTA_COOLDOWN_S)
        client = FakeClient(uploads=[UploadResult(201, action="success", shot_count=1)])
        commands.cmd_push(_linked_config(), tmp_path, client, retry=True, out=lambda _m: None)
        assert client.uploaded == ["a"]

    def test_retry_all_leaves_pushed_sessions_alone(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.mark_pushed(path, "a", 1)
        client = FakeClient(uploads=[])
        commands.cmd_push(_linked_config(), tmp_path, client, retry=True, out=lambda _m: None)
        assert client.uploaded == []
        assert spool.is_pushed(path)

    def test_retry_named_session_force_reuploads_pushed(self, tmp_path):
        path = _write_session(
            tmp_path, "session_20260527_x.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.mark_pushed(path, "a", 0)  # previously "uploaded" with 0 shots
        client = FakeClient(uploads=[UploadResult(201, action="success", shot_count=5)])
        commands.cmd_push(
            _linked_config(), tmp_path, client, retry=True, session="20260527", out=lambda _m: None
        )
        assert client.uploaded == ["a"]
        assert spool.is_pushed(path)

    def test_retry_named_no_match_reports_and_uploads_nothing(self, tmp_path):
        path = _write_session(
            tmp_path, "session_a.jsonl", {"type": "session_start", "session_uuid": "a"}
        )
        spool.mark_pushed(path, "a", 1)
        client = FakeClient(uploads=[])
        out = []
        commands.cmd_push(
            _linked_config(), tmp_path, client, retry=True, session="nope", out=out.append
        )
        assert client.uploaded == []
        assert "no" in "\n".join(out).lower()


class TestLink:
    def test_links_and_saves_config(self, tmp_path):
        config_path = tmp_path / "cloud.json"
        client = FakeClient(
            link_start=LinkStart("ABCD-2345", "poll-tok", 5, 900),
            polls=[
                LinkPoll("pending"),
                LinkPoll("linked", device_token="of_device_new", device_id="dev-99"),
            ],
        )
        out = []
        ok = commands.cmd_link(
            CloudConfig(endpoint="https://e.test"),
            config_path,
            client,
            device_name="garage pi",
            sleep=lambda _s: None,
            out=out.append,
        )
        assert ok is True
        saved = json.loads(config_path.read_text())
        assert saved["device_token"] == "of_device_new"
        assert saved["device_id"] == "dev-99"
        assert saved["enabled"] is True
        assert "ABCD-2345" in "\n".join(out)

    def test_expired_returns_false(self, tmp_path):
        client = FakeClient(
            link_start=LinkStart("ABCD-2345", "poll-tok", 5, 900),
            polls=[LinkPoll("expired")],
        )
        ok = commands.cmd_link(
            CloudConfig(endpoint="https://e.test"),
            tmp_path / "cloud.json",
            client,
            device_name="pi",
            sleep=lambda _s: None,
            out=lambda _m: None,
        )
        assert ok is False
        assert not (tmp_path / "cloud.json").exists()


class TestStatus:
    def test_reports_unlinked(self, tmp_path):
        out = []
        commands.cmd_status(CloudConfig(), tmp_path, out=out.append)
        assert "not linked" in "\n".join(out).lower()

    def test_reports_reachability_when_client_given(self, tmp_path):
        out = []
        client = FakeClient(healthy=False)
        result = commands.cmd_status(_linked_config(), tmp_path, client=client, out=out.append)
        assert result["online"] is False
        assert "unreachable" in "\n".join(out).lower()

    def test_flags_zero_shot_uploads_with_retry_hint(self, tmp_path):
        a = _write_session(tmp_path, "session_zero.jsonl", {"type": "session_start"})
        b = _write_session(tmp_path, "session_ok.jsonl", {"type": "session_start"})
        spool.mark_pushed(a, "id-a", 0)
        spool.mark_pushed(b, "id-b", 12)
        out = []
        result = commands.cmd_status(_linked_config(), tmp_path, out=out.append)
        text = "\n".join(out)
        assert "session_zero.jsonl" in text
        assert "--retry" in text
        assert "session_ok.jsonl" not in text  # healthy uploads aren't nagged
        assert result["zero_shot"] == ["session_zero.jsonl"]

    def test_reports_counts_and_parked(self, tmp_path):
        a = _write_session(tmp_path, "session_a.jsonl", {"type": "session_start"})
        b = _write_session(tmp_path, "session_b.jsonl", {"type": "session_start"})
        spool.mark_pushed(a, "id-a", 2)
        spool.mark_parked(b, reason="invalid_gzip", attempts=3, last_error="422")
        out = []
        commands.cmd_status(_linked_config(), tmp_path, out=out.append)
        text = "\n".join(out)
        assert "dev-1" in text
        assert "invalid_gzip" in text


class TestPushConcurrency:
    def test_second_push_skips_while_first_holds_the_lock(self, tmp_path):
        _write_session(tmp_path, "session_a.jsonl", {"type": "session_start"})
        client = FakeClient()
        with spool.push_lock(tmp_path) as acquired:
            assert acquired
            result = commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        assert result["skipped"] == "busy"
        assert client.uploaded == []

    def test_lock_is_released_after_a_push(self, tmp_path):
        client = FakeClient()
        commands.cmd_push(_linked_config(), tmp_path, client, out=lambda _m: None)
        with spool.push_lock(tmp_path) as acquired:
            assert acquired

    def test_dry_run_does_not_need_the_lock(self, tmp_path):
        _write_session(tmp_path, "session_a.jsonl", {"type": "session_start"})
        with spool.push_lock(tmp_path):
            result = commands.cmd_push(
                _linked_config(), tmp_path, FakeClient(), dry_run=True, out=lambda _m: None
            )
        assert "skipped" not in result

    def test_lock_excludes_other_processes(self, tmp_path):
        import subprocess
        import sys

        script = (
            "import sys, time\n"
            "from openflight.cloud import spool\n"
            "with spool.push_lock(sys.argv[1]) as ok:\n"
            "    print(ok, flush=True)\n"
            "    time.sleep(1.5)\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert holder.stdout.readline().strip() == "True"
            with spool.push_lock(tmp_path) as acquired:
                assert acquired is False
        finally:
            holder.wait(timeout=10)


def test_spool_imports_and_locks_without_fcntl(tmp_path):
    """Windows has no fcntl; the server imports spool at startup, so it must load."""
    import subprocess
    import sys

    script = (
        "import sys\n"
        "import openflight.cloud  # pyserial's POSIX backend needs the real fcntl\n"
        "sys.modules.pop('openflight.cloud.spool', None)\n"
        "sys.modules['fcntl'] = None\n"
        "from openflight.cloud import spool\n"
        "assert spool.fcntl is None\n"
        "with spool.push_lock(sys.argv[1]) as ok:\n"
        "    print(ok)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
