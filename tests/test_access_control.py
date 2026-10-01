"""--auth-required (off by default) and the web-server request limits."""

import argparse
import os
import stat
import sys

import pytest

from openflight import access, server as server_module

LAN = "192.168.1.50"


def _args(**overrides):
    base = dict(auth_required=False, auth_token_file="", allowed_origin=[])
    base.update(overrides)
    return argparse.Namespace(**base)


class TestTokenFile:
    def test_created_private_and_reused(self, tmp_path):
        path = tmp_path / "cfg" / "token"
        first = access.load_or_create_token(path, {})
        second = access.load_or_create_token(path, {})
        assert first == second and len(first) >= 24
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_env_overrides_file(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("from-file\n")
        assert access.load_or_create_token(path, {access.TOKEN_ENV: "env"}) == "env"

    def test_print_token_cli(self, tmp_path, capsys):
        path = tmp_path / "token"
        assert access.main(["print-token", "--token-file", str(path)]) == 0
        assert capsys.readouterr().out.strip() == path.read_text().strip()


class TestPolicyDefaults:
    def test_disabled_by_default_and_touches_nothing(self, tmp_path):
        path = tmp_path / "token"
        policy = access.AccessPolicy.from_args(_args(auth_token_file=str(path)))
        assert policy.enabled is False
        assert not path.exists()
        assert policy.origin_ok("https://evil.example")

    def test_enabled_loads_token_and_local_names(self, tmp_path, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "golfpi")
        monkeypatch.setattr(
            access.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("192.168.1.9", 0))]
        )
        policy = access.AccessPolicy.from_args(
            _args(
                auth_required=True,
                auth_token_file=str(tmp_path / "t"),
                allowed_origin=["http://laptop.lan:5173"],
            ),
            environ={},
        )
        assert policy.enabled and policy.token
        for host in ("golfpi", "golfpi.local", "192.168.1.9", "localhost", "laptop.lan"):
            assert host in policy.allowed_hosts
        assert policy.origin_ok("http://golfpi.local:8080")
        assert policy.origin_ok("http://127.0.0.1:5173")
        assert policy.origin_ok(None)
        assert not policy.origin_ok("https://evil.example")
        assert not policy.origin_ok("file://")

    def test_pi_os_lan_ip_origin_is_accepted(self, tmp_path, monkeypatch):
        """Pi OS maps the hostname to 127.0.1.1, so the LAN IP never reaches allowed_hosts."""
        monkeypatch.setattr(access.socket, "gethostname", lambda: "openflight")
        monkeypatch.setattr(
            access.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("127.0.1.1", 0))]
        )
        monkeypatch.setattr(access, "_primary_ipv4", lambda: None)
        monkeypatch.setattr(access, "_interface_ip_cache", (float("-inf"), frozenset()))
        policy = access.AccessPolicy.from_args(
            _args(auth_required=True, auth_token_file=str(tmp_path / "t")), environ={}
        )
        assert "192.168.0.221" not in policy.allowed_hosts
        assert policy.origin_ok("http://192.168.0.221:8080", "192.168.0.221:8080")
        assert not policy.origin_ok("http://192.168.0.99:8080", "192.168.0.221:8080")
        assert not policy.origin_ok("http://evil.example", "evil.example")
        assert not policy.origin_ok("http://192.168.0.221:8080")

    def test_interface_address_is_accepted_without_matching_host(self, monkeypatch):
        monkeypatch.setattr(access, "_primary_ipv4", lambda: "192.168.0.221")
        monkeypatch.setattr(access, "_interface_ip_cache", (float("-inf"), frozenset()))
        policy = access.AccessPolicy(enabled=True, token="t")
        assert policy.origin_ok("http://192.168.0.221:8080", "openflight.local:8080")
        assert not policy.origin_ok("http://192.168.0.50:8080", "openflight.local:8080")

    def test_interface_addresses_are_cached_briefly(self, monkeypatch):
        calls = []
        monkeypatch.setattr(access, "_primary_ipv4", lambda: calls.append(1) or "10.0.0.7")
        monkeypatch.setattr(access, "_interface_ip_cache", (float("-inf"), frozenset()))
        assert access.interface_addresses(now=100.0) == {"10.0.0.7"}
        assert access.interface_addresses(now=120.0) == {"10.0.0.7"}
        assert len(calls) == 1
        access.interface_addresses(now=131.0)
        assert len(calls) == 2

    def test_loopback_is_exempt(self):
        policy = access.AccessPolicy(enabled=True, token="t")
        assert policy.client_is_exempt("127.0.0.1")
        assert policy.client_is_exempt("::1")
        assert not policy.client_is_exempt(LAN)


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "request_rate_limit_per_s", 0.0)
    monkeypatch.setattr(server_module, "_rate_buckets", {})
    monkeypatch.setattr(
        server_module, "profile_store", server_module.ProfileStore(tmp_path / "p.json")
    )
    yield server_module


@pytest.fixture
def open_server(quiet, monkeypatch):
    monkeypatch.setattr(quiet, "access_policy", access.AccessPolicy.disabled())
    return quiet


@pytest.fixture
def locked_server(quiet, monkeypatch):
    monkeypatch.setattr(
        quiet,
        "access_policy",
        access.AccessPolicy(enabled=True, token="s3cret", allowed_hosts=frozenset({"golfpi"})),
    )
    return quiet


def _http(srv, remote, **kwargs):
    return srv.app.test_client().get(
        "/api/camera/exposure-quality", environ_base={"REMOTE_ADDR": remote}, **kwargs
    )


def _socket(srv, remote, **kwargs):
    flask_client = srv.app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = remote
    return srv.socketio.test_client(srv.app, flask_test_client=flask_client, **kwargs)


class TestFlagOff:
    """Default: everything on the network is accepted, exactly as before."""

    def test_lan_http_without_token_is_served(self, open_server):
        assert _http(open_server, LAN).status_code == 404  # camera not enabled, not 401

    def test_lan_socket_without_token_connects(self, open_server):
        assert _socket(open_server, LAN).is_connected()

    def test_foreign_origin_is_served(self, open_server):
        response = _http(open_server, LAN, headers={"Origin": "https://evil.example"})
        assert response.status_code == 404
        # flask-cors echoes any origin today.
        assert response.headers.get("Access-Control-Allow-Origin") == "https://evil.example"


class TestFlagOn:
    def test_loopback_exempt(self, locked_server):
        assert _http(locked_server, "127.0.0.1").status_code == 404
        assert _socket(locked_server, "127.0.0.1").is_connected()

    def test_token_ok_header_query_and_socket_auth(self, locked_server):
        assert _http(locked_server, LAN, headers={access.TOKEN_HEADER: "s3cret"}).status_code == 404
        assert (
            _http(locked_server, LAN, headers={"Authorization": "Bearer s3cret"}).status_code == 404
        )
        assert _http(locked_server, LAN, query_string={"token": "s3cret"}).status_code == 404
        assert _socket(locked_server, LAN, auth={"token": "s3cret"}).is_connected()
        assert _socket(locked_server, LAN, query_string="?token=s3cret").is_connected()

    def test_token_missing_or_wrong(self, locked_server):
        assert _http(locked_server, LAN).status_code == 401
        assert _http(locked_server, LAN, headers={access.TOKEN_HEADER: "nope"}).status_code == 401
        assert not _socket(locked_server, LAN).is_connected()
        assert not _socket(locked_server, LAN, auth={"token": "nope"}).is_connected()

    def test_bad_origin_even_with_token_and_from_loopback(self, locked_server):
        response = _http(
            locked_server,
            LAN,
            headers={access.TOKEN_HEADER: "s3cret", "Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert (
            _http(
                locked_server, "127.0.0.1", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert not _socket(
            locked_server, LAN, headers={"Origin": "https://evil.example"}, auth={"token": "s3cret"}
        ).is_connected()

    def test_ui_shell_loads_without_token_but_api_and_socket_do_not(
        self, locked_server, monkeypatch
    ):
        """A phone must be able to load the page and hand the token to the socket."""
        monkeypatch.setattr(
            locked_server, "_react_app_dir", lambda: locked_server.FRONTEND_SOURCE_DIR
        )
        client = locked_server.app.test_client()
        for path in ("/", "/display", "/index.html"):
            response = client.get(path, environ_base={"REMOTE_ADDR": LAN})
            assert response.status_code != 401, path
        assert _http(locked_server, LAN).status_code == 401
        post = client.post("/api/shutdown", environ_base={"REMOTE_ADDR": LAN})
        assert post.status_code == 401
        assert not _socket(locked_server, LAN).is_connected()

    def test_allowed_origin_is_served(self, locked_server):
        response = _http(
            locked_server,
            LAN,
            headers={access.TOKEN_HEADER: "s3cret", "Origin": "http://golfpi:8080"},
        )
        assert response.status_code == 404

    def test_phone_opening_the_pi_by_ip_is_served(self, locked_server, monkeypatch):
        monkeypatch.setattr(access, "_primary_ipv4", lambda: None)
        monkeypatch.setattr(access, "_interface_ip_cache", (float("-inf"), frozenset()))
        response = _http(
            locked_server,
            LAN,
            headers={access.TOKEN_HEADER: "s3cret", "Origin": "http://192.168.0.221:8080"},
            base_url="http://192.168.0.221:8080",
        )
        assert response.status_code == 404


class TestRateLimit:
    def test_off_by_default(self, open_server):
        for _ in range(50):
            assert _http(open_server, LAN).status_code != 429

    def test_lan_ip_is_capped_but_loopback_is_not(self, open_server, monkeypatch):
        monkeypatch.setattr(open_server, "request_rate_limit_per_s", 2.0)  # 4 per 2 s window
        codes = [_http(open_server, LAN).status_code for _ in range(6)]
        assert codes[:4] == [404] * 4 and codes[4:] == [429, 429]
        assert all(_http(open_server, "127.0.0.1").status_code == 404 for _ in range(10))

    def test_fractional_limit_still_admits_one_request(self, open_server, monkeypatch):
        monkeypatch.setattr(open_server, "request_rate_limit_per_s", 0.4)
        assert not open_server._rate_limited(LAN, now=100.0)
        assert open_server._rate_limited(LAN, now=100.1)

    def test_idle_ips_are_forgotten(self, open_server, monkeypatch):
        monkeypatch.setattr(open_server, "request_rate_limit_per_s", 1.0)
        monkeypatch.setattr(open_server, "RATE_LIMIT_MAX_TRACKED_IPS", 2)
        for index in range(3):
            open_server._rate_limited(f"10.0.0.{index}", now=100.0)
        open_server._rate_limited("10.0.0.9", now=200.0)
        assert set(open_server._rate_buckets) == {"10.0.0.9"}

    def test_window_expires(self, open_server, monkeypatch):
        monkeypatch.setattr(open_server, "request_rate_limit_per_s", 1.0)
        assert not open_server._rate_limited(LAN, now=100.0)
        assert not open_server._rate_limited(LAN, now=100.1)
        assert open_server._rate_limited(LAN, now=100.2)
        assert not open_server._rate_limited(LAN, now=102.5)


class TestMaxRequestBytes:
    def _run_main(self, monkeypatch, *extra):
        runs = []
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--mock", "--no-logging", *extra])
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *a, **kw: runs.append(kw))
        monkeypatch.setattr(server_module, "air_density", server_module.air_density)
        server_module.main()
        return runs[0]

    def test_defaults_keep_open_server(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server_module, "access_policy", access.AccessPolicy.disabled())
        run = self._run_main(monkeypatch, "--auth-token-file", str(tmp_path / "t"))
        assert server_module.access_policy.enabled is False
        assert server_module.request_rate_limit_per_s == 0.0
        assert server_module.app.config["MAX_CONTENT_LENGTH"] is None
        assert not (tmp_path / "t").exists()
        assert run["host"] == "0.0.0.0" and run["allow_unsafe_werkzeug"] is True

    def test_flags_apply(self, monkeypatch, tmp_path):
        monkeypatch.delenv(access.TOKEN_ENV, raising=False)
        try:
            self._run_main(
                monkeypatch,
                "--auth-required",
                "--auth-token-file",
                str(tmp_path / "t"),
                "--request-rate-limit",
                "5",
                "--max-request-bytes",
                "4096",
            )
            assert server_module.access_policy.enabled is True
            assert server_module.access_policy.token == (tmp_path / "t").read_text().strip()
            assert server_module.request_rate_limit_per_s == 5.0
            assert server_module.app.config["MAX_CONTENT_LENGTH"] == 4096
            big = server_module.app.test_client().post(
                "/api/shutdown", data=b"x" * 8192, environ_base={"REMOTE_ADDR": "127.0.0.1"}
            )
            assert big.status_code == 413
        finally:
            server_module.access_policy = access.AccessPolicy.disabled()
            server_module.request_rate_limit_per_s = 0.0
            server_module.app.config["MAX_CONTENT_LENGTH"] = None


def test_werkzeug_override_is_still_required_under_systemd(monkeypatch):
    """Flask-SocketIO refuses Werkzeug without the override when stdin is not a TTY."""
    import flask
    import flask_socketio

    monkeypatch.setattr(sys, "stdin", None)
    probe = flask.Flask("werkzeug-probe")
    sio = flask_socketio.SocketIO(probe, async_mode="threading")
    with pytest.raises(RuntimeError, match="allow_unsafe_werkzeug"):
        sio.run(probe, host="127.0.0.1", port=0)
