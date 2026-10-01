"""--update-check: the server-side service and its socket events, flag off and on."""

import json
import math
import sys
import time

import pytest

from openflight import server as server_module, update_service as svc_module
from openflight.update_service import KIOSK_ONLY, UPDATE_EXIT_CODE, UpdateService
from openflight.updater import (
    STATE_AVAILABLE,
    STATE_FAILED,
    STATE_RESTARTING,
    STATE_UP_TO_DATE,
    ApplyResult,
    UpdateError,
    UpdateStatus,
)

LAN = "192.168.1.50"


class FakeUpdater:
    def __init__(self, state=STATE_AVAILABLE, result=None, raises=None):
        self.status = UpdateStatus(state=state, current="aaaaaaa", latest="bbbbbbb", behind=2)
        self.result = result or ApplyResult(True, "a" * 40, "b" * 40)
        self.raises = raises
        self.checks = 0
        self.steps_seen = []
        self.stop_requests = 0
        self.during_apply = None

    def request_stop(self):
        self.stop_requests += 1

    def check(self):
        self.checks += 1
        self.status.state = STATE_UP_TO_DATE
        return self.status

    def preflight_error(self):
        if self.status.state != STATE_AVAILABLE:
            return "No update is available; check for updates first"
        return None

    def apply(self, progress):
        progress("Downloading the update")
        self.steps_seen.append(self.status.step)
        if self.during_apply:
            self.during_apply()
        if self.raises:
            raise self.raises
        return self.result


class Harness:
    def __init__(self, tmp_path, updater=None, idle=math.inf):
        self.emitted = []
        self.events = []
        self.exit_codes = []
        self.updater = updater or FakeUpdater()
        self.service = UpdateService(
            self.updater,
            emit=lambda event, payload, sid: self.emitted.append((event, payload, sid)),
            stop_hardware=lambda: self.events.append("stop_hardware"),
            exit_process=self.exit_codes.append,
            seconds_since_activity=lambda: idle,
            restart_delay_s=0.0,
            result_file=tmp_path / "state" / "last_update.json",
            start_thread=lambda target: target(),  # run synchronously
        )

    def last(self, sid=None):
        payloads = [p for e, p, s in self.emitted if e == "update_status" and s == sid]
        return payloads[-1]


class TestUpdateService:
    def test_connect_sends_status_with_per_client_apply_right(self, tmp_path):
        h = Harness(tmp_path)
        h.service.client_connected("kiosk", True)
        h.service.client_connected("phone", False)
        assert h.last("kiosk")["can_apply"] is True
        assert h.last("phone")["can_apply"] is False
        assert h.last("kiosk")["state"] == STATE_AVAILABLE and h.last("kiosk")["behind"] == 2
        h.service.publish()
        assert {s for e, _, s in h.emitted[-2:]} == {"kiosk", "phone"}
        h.service.client_disconnected("phone")
        h.emitted.clear()
        h.service.publish()
        assert [s for _, _, s in h.emitted] == ["kiosk"]

    def test_non_kiosk_can_neither_check_nor_apply(self, tmp_path):
        h = Harness(tmp_path)
        assert h.service.request_check(False) == KIOSK_ONLY
        assert h.service.request_apply(False) == KIOSK_ONLY
        assert h.updater.checks == 0 and h.events == [] and h.exit_codes == []

    def test_kiosk_check_runs_and_publishes(self, tmp_path):
        h = Harness(tmp_path)
        h.service.client_connected("kiosk", True)
        assert h.service.request_check(True) is None
        assert h.updater.checks == 1
        assert h.last("kiosk")["state"] == STATE_UP_TO_DATE

    def test_apply_refused_while_a_shot_is_recent(self, tmp_path):
        h = Harness(tmp_path, idle=2.0)
        assert "shot" in h.service.request_apply(True)
        assert h.events == []

    def test_apply_refused_without_an_available_update(self, tmp_path):
        h = Harness(tmp_path, updater=FakeUpdater(state=STATE_UP_TO_DATE))
        assert "No update" in h.service.request_apply(True)
        assert h.events == [] and h.exit_codes == []

    def test_successful_apply_stops_hardware_saves_result_and_exits_for_restart(self, tmp_path):
        h = Harness(tmp_path)
        h.service.client_connected("kiosk", True)
        assert h.service.request_apply(True) is None
        assert h.events == ["stop_hardware"]
        assert h.updater.steps_seen == ["Downloading the update"]
        assert h.last("kiosk")["state"] == STATE_RESTARTING
        assert h.exit_codes == [UPDATE_EXIT_CODE]
        saved = json.loads((tmp_path / "state" / "last_update.json").read_text())
        assert saved["ok"] is True and saved["installed"] == "bbbbbbb"
        # The restarted server shows the outcome.
        assert Harness(tmp_path).service.payload(True)["last_result"]["installed"] == "bbbbbbb"

    def test_failed_apply_reports_rollback_and_still_restarts(self, tmp_path):
        failed = ApplyResult(False, "a" * 40, None, "npm run build failed", "x", rolled_back=True)
        h = Harness(tmp_path, updater=FakeUpdater(result=failed))
        h.service.client_connected("kiosk", True)
        h.service.request_apply(True)
        payload = h.last("kiosk")
        assert payload["state"] == STATE_FAILED and payload["rolled_back"] is True
        assert payload["error"] == "npm run build failed"
        assert payload["last_result"]["ok"] is False
        assert h.exit_codes == [UPDATE_EXIT_CODE]

    def test_shutdown_while_idle_is_not_intercepted(self, tmp_path):
        h = Harness(tmp_path)
        assert h.service.request_stop_apply() is False
        assert h.updater.stop_requests == 0

    def test_shutdown_during_an_install_asks_it_to_roll_back(self, tmp_path):
        h = Harness(tmp_path)
        answers = []
        h.updater.during_apply = lambda: answers.append(h.service.request_stop_apply())
        h.service.request_apply(True)
        assert answers == [True]
        assert h.updater.stop_requests == 1
        assert h.exit_codes == [UPDATE_EXIT_CODE]

    def test_updater_exception_is_contained(self, tmp_path):
        h = Harness(
            tmp_path, updater=FakeUpdater(raises=UpdateError("Another update is already running"))
        )
        h.service.request_apply(True)
        assert h.updater.status.state == STATE_FAILED
        assert h.exit_codes == [UPDATE_EXIT_CODE]

    def test_check_loop_waits_then_checks_until_stopped(self, tmp_path):
        h = Harness(tmp_path)
        service = UpdateService(
            h.updater,
            emit=lambda *a: None,
            stop_hardware=lambda: None,
            exit_process=lambda code: None,
            first_check_delay_s=0.0,
            check_interval_s=0.0,
            result_file=None,
        )
        service.start()
        deadline = time.monotonic() + 2
        while h.updater.checks == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        service.stop()
        assert h.updater.checks >= 1

    def test_restart_mode_follows_systemd(self):
        assert svc_module.restart_mode_from_env({"INVOCATION_ID": "abc"}) == "systemd"
        assert svc_module.restart_mode_from_env({}) == "manual"

    def test_corrupt_result_file_is_ignored(self, tmp_path):
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "last_update.json").write_text("{not json")
        assert Harness(tmp_path).service.last_result is None


# -- server wiring -----------------------------------------------------------


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module, "profile_store", server_module.ProfileStore(tmp_path / "p.json")
    )
    monkeypatch.setattr(server_module, "_last_shot_activity", None)
    return server_module


def _socket(srv, remote):
    flask_client = srv.app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = remote
    return srv.socketio.test_client(srv.app, flask_test_client=flask_client)


def _events(client, name):
    return [msg["args"][0] for msg in client.get_received() if msg["name"] == name]


class TestServerFlagOff:
    def test_status_is_disabled_and_actions_refused(self, quiet, monkeypatch):
        monkeypatch.setattr(quiet, "update_service", None)
        client = _socket(quiet, "127.0.0.1")
        assert _events(client, "update_status") == []  # nothing pushed on connect
        client.emit("get_update_status")
        assert _events(client, "update_status") == [{**quiet.disabled_status()}]
        client.emit("apply_update")
        client.emit("check_for_updates")
        errors = _events(client, "update_error")
        assert len(errors) == 2 and all("--update-check" in e["error"] for e in errors)


class TestServerFlagOn:
    @pytest.fixture
    def harness(self, quiet, monkeypatch, tmp_path):
        h = Harness(tmp_path)

        def emit(event, payload, sid):
            h.emitted.append((event, payload, sid))
            quiet._emit_to(event, payload, sid)

        h.service._emit = emit
        monkeypatch.setattr(quiet, "update_service", h.service)
        return h

    def test_kiosk_and_phone_get_different_rights(self, quiet, harness):
        kiosk = _socket(quiet, "127.0.0.1")
        phone = _socket(quiet, LAN)
        assert _events(kiosk, "update_status")[-1]["can_apply"] is True
        assert _events(phone, "update_status")[-1]["can_apply"] is False
        phone.emit("get_update_status")
        assert _events(phone, "update_status")[-1]["can_apply"] is False

    def test_phone_cannot_apply(self, quiet, harness):
        phone = _socket(quiet, LAN)
        phone.emit("apply_update")
        assert _events(phone, "update_error") == [{"error": KIOSK_ONLY}]
        assert harness.exit_codes == [] and harness.events == []

    def test_kiosk_apply_runs_the_update(self, quiet, harness):
        kiosk = _socket(quiet, "127.0.0.1")
        kiosk.emit("apply_update")
        assert harness.events == ["stop_hardware"]
        assert harness.exit_codes == [UPDATE_EXIT_CODE]
        states = [p["state"] for p in _events(kiosk, "update_status")]
        assert "updating" in states and states[-1] == STATE_RESTARTING

    def test_recent_shot_blocks_apply(self, quiet, harness, monkeypatch):
        harness.service._seconds_since_activity = quiet._seconds_since_shot_activity
        quiet.on_shot_processing("started")
        kiosk = _socket(quiet, "127.0.0.1")
        kiosk.emit("apply_update")
        assert "shot" in _events(kiosk, "update_error")[0]["error"]
        assert harness.exit_codes == []


class TestMainFlag:
    def _run_main(self, monkeypatch, *extra):
        started = []
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", *extra])
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *a, **kw: None)
        monkeypatch.setattr(server_module, "init_update_service", started.append)
        server_module.main()
        return started

    def test_off_by_default(self, monkeypatch):
        assert self._run_main(monkeypatch) == []

    def test_flag_starts_the_service_with_its_options(self, monkeypatch):
        started = self._run_main(
            monkeypatch, "--update-check", "--update-branch", "stable", "--update-check-hours", "12"
        )
        assert len(started) == 1
        args = started[0]
        assert (args.update_remote, args.update_branch, args.update_check_hours) == (
            "origin",
            "stable",
            12.0,
        )

    def test_init_update_service_builds_the_real_updater(self, monkeypatch, tmp_path):
        import argparse

        monkeypatch.setattr(svc_module.UpdateService, "start", lambda self: None)
        args = argparse.Namespace(
            update_remote="origin",
            update_branch="main",
            update_check_hours=6.0,
            camera_capture=True,
        )
        try:
            service = server_module.init_update_service(args)
            assert server_module.update_service is service
            config = service.updater.config
            assert config.project_dir == server_module.REPO_ROOT
            assert config.python_extras == ("camera",)
            # The installed version is known before the first network check.
            assert service.updater.status.current
        finally:
            server_module.update_service = None
