"""Unit tests for the web server access-control helpers."""

import os
import stat

import pytest

from openflight import access


class TestLoopback:
    @pytest.mark.parametrize("address", ["127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1"])
    def test_loopback_addresses(self, address):
        assert access.is_loopback_address(address)

    @pytest.mark.parametrize("address", ["192.168.1.20", "10.0.0.1", "::ffff:10.0.0.1", "", None])
    def test_non_loopback_addresses(self, address):
        assert not access.is_loopback_address(address)

    def test_garbage_is_not_loopback(self):
        assert not access.is_loopback_address("localhost-ish")


class TestHostAllowed:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8080",
            "127.0.0.1:8080",
            "192.168.1.44:8080",
            "[::1]:8080",
            "openflight.local:8080",
            "OpenFlight.LOCAL",
        ],
    )
    def test_hosts_a_real_client_uses(self, host):
        assert access.host_is_allowed(host)

    def test_own_hostname_is_allowed(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "golfpi")
        assert access.host_is_allowed("golfpi:8080")

    @pytest.mark.parametrize("host", ["golfpi.lan:8080", "golfpi.home", "golfpi.localdomain"])
    def test_own_hostname_with_router_domain_is_allowed(self, monkeypatch, host):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "golfpi")
        assert access.host_is_allowed(host)

    def test_other_names_under_a_router_domain_are_refused(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "golfpi")
        assert not access.host_is_allowed("golfpi-evil.example")
        assert not access.host_is_allowed("notgolfpi.lan")
        assert not access.host_is_allowed("golfpi.attacker.example")

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "", None])
    def test_rebinding_style_hosts_are_refused(self, host):
        assert not access.host_is_allowed(host)

    def test_extra_hosts_are_honored(self):
        assert access.host_is_allowed("golf.home.arpa:8080", ["golf.home.arpa"])


class TestOriginAllowed:
    def test_missing_origin_is_non_browser_and_allowed(self):
        assert access.origin_is_allowed(None, "localhost:8080")

    def test_same_origin(self):
        assert access.origin_is_allowed("http://192.168.1.5:8080", "192.168.1.5:8080")

    @pytest.mark.parametrize(
        "origin", ["http://localhost:5173", "http://127.0.0.1:8080", "http://[::1]:5173"]
    )
    def test_loopback_origins(self, origin):
        assert access.origin_is_allowed(origin, "localhost:8080")

    def test_foreign_origin_refused(self):
        assert not access.origin_is_allowed("https://evil.example", "192.168.1.5:8080")

    def test_non_http_scheme_refused(self):
        assert not access.origin_is_allowed("file://", "localhost:8080")

    def test_configured_origin(self):
        assert access.origin_is_allowed(
            "http://laptop.lan:5173/", "192.168.1.5:8080", ["http://laptop.lan:5173"]
        )


class TestTokenMatches:
    def test_exact_match(self):
        assert access.token_matches("abc123", "abc123")

    @pytest.mark.parametrize("provided", ["abc124", "", None, 123, ["abc123"]])
    def test_mismatch(self, provided):
        assert not access.token_matches(provided, "abc123")

    def test_no_expected_token_never_matches(self):
        assert not access.token_matches("", None)
        assert not access.token_matches("anything", None)


class TestLoadOrCreateToken:
    def test_env_overrides_file(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("from-file\n")
        assert access.load_or_create_token(path, {access.TOKEN_ENV: " env-token "}) == "env-token"

    def test_existing_file_is_reused(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("kept\n")
        assert access.load_or_create_token(path, {}) == "kept"

    def test_creates_private_random_token_once(self, tmp_path):
        path = tmp_path / "nested" / "token"
        first = access.load_or_create_token(path, {})
        second = access.load_or_create_token(path, {})
        assert first == second
        assert len(first) >= 24
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_empty_file_gets_a_persisted_token(self, tmp_path):
        path = tmp_path / "token"
        path.write_text("\n")
        path.chmod(0o644)
        token = access.load_or_create_token(path, {})
        assert token
        assert path.read_text().strip() == token
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
