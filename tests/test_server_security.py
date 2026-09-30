"""Server-level access control, reply targeting, and input validation."""

import pytest

from openflight import server as server_module

REMOTE = "192.168.1.50"


@pytest.fixture
def access(monkeypatch):
    """Isolated access policy with a known token and no connected controllers."""
    monkeypatch.setattr(server_module, "access_token", "s3cret-token")
    monkeypatch.setattr(server_module, "allowed_extra_origins", [])
    monkeypatch.setattr(server_module, "allowed_extra_hosts", [])
    monkeypatch.setattr(server_module, "_controller_sids", set())
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "power_monitor", None)
    return server_module


def _socket_client(remote_addr=REMOTE, **kwargs):
    flask_client = server_module.app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = remote_addr
    return server_module.socketio.test_client(
        server_module.app, flask_test_client=flask_client, **kwargs
    )


def _events(client, name):
    return [event["args"][0] for event in client.get_received() if event["name"] == name]


class TestHttpAccess:
    def test_remote_shutdown_without_token_is_refused(self, access, monkeypatch):
        started = []
        monkeypatch.setattr(access.threading, "Thread", lambda **kw: started.append(kw))
        response = access.app.test_client().post(
            "/api/shutdown", environ_base={"REMOTE_ADDR": REMOTE}
        )
        assert response.status_code == 403
        assert started == []

    def test_remote_shutdown_with_token_header_is_allowed(self, access, monkeypatch):
        class FakeThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

        monkeypatch.setattr(access.threading, "Thread", FakeThread)
        response = access.app.test_client().post(
            "/api/shutdown",
            environ_base={"REMOTE_ADDR": REMOTE},
            headers={"X-OpenFlight-Token": "s3cret-token"},
        )
        assert response.status_code == 200

    def test_wrong_token_is_refused(self, access):
        response = access.app.test_client().post(
            "/api/shutdown",
            environ_base={"REMOTE_ADDR": REMOTE},
            headers={"Authorization": "Bearer nope"},
        )
        assert response.status_code == 403

    def test_remote_replay_prepare_is_refused_without_token(self, access, monkeypatch):
        monkeypatch.setattr(access, "camera_replay_manager", object())
        response = access.app.test_client().post(
            "/api/camera/replays/abc/prepare", environ_base={"REMOTE_ADDR": REMOTE}
        )
        assert response.status_code == 403

    def test_cross_site_post_from_kiosk_browser_is_refused(self, access, monkeypatch):
        """A foreign page open in the kiosk browser must not shut the device down."""
        monkeypatch.setattr(access.threading, "Thread", pytest.fail)
        response = access.app.test_client().post(
            "/api/shutdown", headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403

    def test_same_origin_post_from_kiosk_is_allowed(self, access, monkeypatch):
        class FakeThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

        monkeypatch.setattr(access.threading, "Thread", FakeThread)
        response = access.app.test_client().post(
            "/api/shutdown", headers={"Origin": "http://localhost"}
        )
        assert response.status_code == 200

    def test_untrusted_host_header_is_rejected(self, access):
        response = access.app.test_client().get(
            "/api/camera/exposure-quality", headers={"Host": "evil.example"}
        )
        assert response.status_code == 421

    def test_cors_header_only_for_allowed_origins(self, access, monkeypatch):
        monkeypatch.setattr(access, "camera_capture_runtime", None)
        client = access.app.test_client()
        allowed = client.get(
            "/api/camera/exposure-quality", headers={"Origin": "http://localhost:5173"}
        )
        foreign = client.get(
            "/api/camera/exposure-quality", headers={"Origin": "https://evil.example"}
        )
        assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        assert "Access-Control-Allow-Origin" not in foreign.headers


class TestSocketAccess:
    def test_loopback_client_can_control(self, access):
        client = _socket_client(remote_addr="127.0.0.1")
        client.emit("set_club", {"club": "7-iron"})
        assert _events(client, "club_changed") == [{"club": "7-iron"}]
        assert _events(client, "permission_denied") == []

    def test_remote_client_is_read_only(self, access):
        client = _socket_client()
        assert client.is_connected()
        client.emit("set_club", {"club": "7-iron"})
        received = client.get_received()
        names = [event["name"] for event in received]
        assert "permission_denied" in names
        assert "club_changed" not in names

    @pytest.mark.parametrize(
        "event",
        [
            "set_club",
            "clear_session",
            "delete_shot",
            "toggle_debug",
            "set_radar_config",
            "set_camera_capture_settings",
            "shutdown",
            "upload_cloud",
            "add_profile",
            "remove_profile",
        ],
    )
    def test_every_state_changing_event_is_gated(self, access, event, monkeypatch):
        monkeypatch.setattr(access.threading, "Thread", pytest.fail)
        client = _socket_client()
        client.get_received()
        client.emit(event, {})
        assert _events(client, "permission_denied")

    def test_remote_client_with_token_can_control(self, access):
        client = _socket_client(auth={"token": "s3cret-token"})
        client.emit("set_club", {"club": "driver"})
        assert _events(client, "club_changed") == [{"club": "driver"}]

    def test_remote_client_with_token_disabled_stays_read_only(self, access, monkeypatch):
        monkeypatch.setattr(access, "access_token", None)
        client = _socket_client(auth={"token": ""})
        client.emit("set_club", {"club": "driver"})
        assert _events(client, "permission_denied")

    def test_socket_from_untrusted_host_is_rejected(self, access):
        client = _socket_client(headers={"Host": "evil.example"})
        assert not client.is_connected()

    def test_disconnect_forgets_controller(self, access):
        client = _socket_client(remote_addr="127.0.0.1")
        assert len(access._controller_sids) == 1
        client.disconnect()
        assert access._controller_sids == set()


class TestReplyTargeting:
    def test_get_requests_answer_only_the_requester(self, access):
        first = _socket_client(remote_addr="127.0.0.1")
        second = _socket_client(remote_addr="127.0.0.1")
        first.get_received()
        second.get_received()

        first.emit("get_radar_config")

        assert len(_events(first, "radar_config")) == 1
        assert _events(second, "radar_config") == []

    def test_connect_state_goes_only_to_the_new_client(self, access):
        first = _socket_client(remote_addr="127.0.0.1")
        first.get_received()
        _socket_client(remote_addr="127.0.0.1")
        assert _events(first, "profiles") == []

    def test_errors_go_only_to_the_requester(self, access):
        first = _socket_client(remote_addr="127.0.0.1")
        second = _socket_client(remote_addr="127.0.0.1")
        second.get_received()
        first.emit("delete_shot", {"timestamp": "missing"})
        assert _events(first, "delete_shot_error")
        assert _events(second, "delete_shot_error") == []

    def test_state_changes_are_still_broadcast(self, access):
        first = _socket_client(remote_addr="127.0.0.1")
        second = _socket_client(remote_addr="127.0.0.1")
        second.get_received()
        first.emit("set_club", {"club": "pw"})
        assert _events(second, "club_changed") == [{"club": "pw"}]


class TestRadarConfigValidation:
    def test_valid_update_is_parsed(self):
        update = server_module.validate_radar_config_update(
            {"min_speed": "20", "max_speed": 150.0, "transmit_power": 3},
            {"min_speed": 10, "max_speed": 220},
        )
        assert update == {"min_speed": 20, "max_speed": 150, "transmit_power": 3}

    @pytest.mark.parametrize(
        "payload",
        [
            {"min_speed": -1},
            {"min_speed": 10_000},
            {"max_speed": 301},
            {"min_magnitude": 10_001},
            {"transmit_power": 8},
            {"min_speed": True},
            {"min_speed": 12.5},
            {"min_speed": float("inf")},
            {"min_speed": float("nan")},
            {"min_speed": "10\r\nA!"},
            {"min_speed": None},
            {"min_speed": [1]},
            {"min_speed": 200, "max_speed": 150},
        ],
    )
    def test_invalid_updates_are_rejected(self, payload):
        with pytest.raises(server_module.RadarConfigError):
            server_module.validate_radar_config_update(payload, {"min_speed": 10, "max_speed": 220})

    def test_non_dict_rejected(self):
        with pytest.raises(server_module.RadarConfigError):
            server_module.validate_radar_config_update("min_speed=5", {})

    def test_zero_max_speed_means_no_ceiling(self):
        update = server_module.validate_radar_config_update(
            {"min_speed": 200, "max_speed": 0}, {"min_speed": 10, "max_speed": 220}
        )
        assert update == {"min_speed": 200, "max_speed": 0}

    def test_invalid_field_sends_nothing_to_the_radar(self, monkeypatch):
        calls = []
        emitted = []

        class Radar:
            def set_min_speed_filter(self, value):
                calls.append(value)

            def set_transmit_power(self, value):
                calls.append(value)

        class Monitor:
            radar = Radar()

        monkeypatch.setattr(server_module, "monitor", Monitor())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 10, "max_speed": 220})
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **_kw: emitted.append(args)
        )

        server_module.handle_set_radar_config({"min_speed": 30, "transmit_power": 99})

        assert calls == []
        assert server_module.radar_config["min_speed"] == 10
        assert emitted[-1][0] == "radar_config_error"
        assert "transmit_power" in emitted[-1][1]["error"]

    def test_hardware_errors_are_not_echoed_to_clients(self, monkeypatch):
        emitted = []

        class Radar:
            def set_min_speed_filter(self, _value):
                raise OSError("/dev/ttyAMA0: secret internal detail")

        class Monitor:
            radar = Radar()

        monkeypatch.setattr(server_module, "monitor", Monitor())
        monkeypatch.setattr(server_module, "mock_mode", False)
        monkeypatch.setattr(server_module, "radar_config", {"min_speed": 10, "max_speed": 220})
        monkeypatch.setattr(server_module, "log_session_error", lambda *_a, **_kw: None)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **_kw: emitted.append(args)
        )

        server_module.handle_set_radar_config({"min_speed": 30})

        assert emitted[-1][0] == "radar_config_error"
        assert "ttyAMA0" not in emitted[-1][1]["error"]


class TestCloudUploadErrors:
    def test_exception_text_is_not_sent_to_the_client(self, monkeypatch):
        emitted = []
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda *args, **kw: emitted.append((args, kw))
        )

        def boom(*_a, **_kw):
            raise RuntimeError("token=abcdef leaked")

        monkeypatch.setattr("openflight.cloud.commands.cmd_push", boom)
        monkeypatch.setattr("openflight.cloud.config.load_config", lambda: None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        server_module._run_cloud_push_for_ui("sid-1")

        final_args, final_kwargs = emitted[-1]
        assert final_args[0] == "cloud_upload_status"
        assert final_args[1]["state"] == "error"
        assert "abcdef" not in final_args[1]["message"]
        assert final_kwargs == {"to": "sid-1"}


class TestDefaultBind:
    def test_server_binds_loopback_by_default(self, monkeypatch, tmp_path):
        import sys

        runs = []
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging"])
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *a, **kw: runs.append(kw))
        monkeypatch.setattr(
            server_module,
            "load_or_create_token",
            lambda *_a, **_kw: pytest.fail("loopback bind must not create a token"),
        )

        server_module.main()

        assert runs[0]["host"] == "127.0.0.1"
        assert server_module.access_token is None

    def test_lan_bind_loads_token(self, monkeypatch, tmp_path):
        import sys

        token_file = tmp_path / "token"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "openflight-server",
                "--mock",
                "--no-logging",
                "--host",
                "0.0.0.0",
                "--auth-token-file",
                str(token_file),
            ],
        )
        monkeypatch.delenv("OPENFLIGHT_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *a, **kw: None)

        server_module.main()

        assert server_module.access_token == token_file.read_text().strip()
        server_module.configure_access(token=None)
