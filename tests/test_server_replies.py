"""Socket reply targeting, radar config validation, and error sanitization."""

import pytest

from openflight import server as server_module


@pytest.fixture
def access(monkeypatch):
    """Server module with no hardware attached."""
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "power_monitor", None)
    return server_module


def _socket_client(**kwargs):
    return server_module.socketio.test_client(server_module.app, **kwargs)


def _events(client, name):
    return [event["args"][0] for event in client.get_received() if event["name"] == name]


class TestReplyTargeting:
    def test_get_requests_answer_only_the_requester(self, access):
        first = _socket_client()
        second = _socket_client()
        first.get_received()
        second.get_received()

        first.emit("get_radar_config")

        assert len(_events(first, "radar_config")) == 1
        assert _events(second, "radar_config") == []

    def test_connect_state_goes_only_to_the_new_client(self, access):
        first = _socket_client()
        first.get_received()
        _socket_client()
        assert _events(first, "profiles") == []

    def test_errors_go_only_to_the_requester(self, access):
        first = _socket_client()
        second = _socket_client()
        second.get_received()
        first.emit("delete_shot", {"timestamp": "missing"})
        assert _events(first, "delete_shot_error")
        assert _events(second, "delete_shot_error") == []

    def test_unknown_club_is_answered_with_an_error(self, access):
        first = _socket_client()
        second = _socket_client()
        second.get_received()
        first.emit("set_club", {"club": "9-wood-typo"})
        assert _events(first, "club_error") == [{"error": "Unknown club", "club": "9-wood-typo"}]
        assert _events(first, "club_changed") == []
        assert _events(second, "club_error") == []

    def test_state_changes_are_still_broadcast(self, access):
        first = _socket_client()
        second = _socket_client()
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
