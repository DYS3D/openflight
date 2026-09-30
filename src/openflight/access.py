"""Access control for the local web server.

The kiosk browser runs on the same machine as the server, so loopback clients
may change device state. Everything else on the network is read-only (TV/tablet
display mode) unless it presents the device access token.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import socket
from pathlib import Path
from typing import Iterable, Mapping, Optional
from urllib.parse import urlsplit

TOKEN_ENV = "OPENFLIGHT_AUTH_TOKEN"
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "openflight" / "auth_token"
TOKEN_HEADER = "X-OpenFlight-Token"
# Home-router search domains. Only this device's own name is accepted under
# them; a public domain would let DNS rebinding reach the server.
LOCAL_DOMAIN_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".localdomain", ".internal")


def is_loopback_address(address: Optional[str]) -> bool:
    """True for 127.0.0.0/8, ::1, and IPv4-mapped loopback addresses."""
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_loopback


def _split_host(host: str) -> str:
    """Hostname part of a Host header or netloc, without port or IPv6 brackets."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def host_is_allowed(host_header: Optional[str], extra_hosts: Iterable[str] = ()) -> bool:
    """Reject Host names an attacker could rebind to this device via DNS.

    IP literals, localhost, mDNS ``.local`` names, and this machine's hostname
    (bare or with a home-router domain such as ``.lan``) are what a real
    kiosk, tablet, or TV uses to reach OpenFlight.
    """
    if not host_header:
        return False
    hostname = _split_host(host_header)
    if not hostname:
        return False
    allowed = {h.strip().lower() for h in extra_hosts if h.strip()}
    local_name = socket.gethostname().lower()
    return (
        _is_ip_literal(hostname)
        or hostname == "localhost"
        or hostname.endswith(".local")
        or hostname == local_name
        or any(hostname == f"{local_name}{suffix}" for suffix in LOCAL_DOMAIN_SUFFIXES)
        or hostname in allowed
    )


def origin_is_allowed(
    origin: Optional[str],
    host_header: Optional[str],
    extra_origins: Iterable[str] = (),
) -> bool:
    """Same-origin, loopback, or explicitly configured.

    Loopback covers the kiosk and a Vite dev server on the same machine; a dev
    server reached over the LAN needs ``--cors-origin``.
    """
    if not origin:
        # Non-browser clients (curl, the kiosk's shutdown hook) send no Origin.
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    normalized = origin.rstrip("/").lower()
    if normalized in {o.rstrip("/").lower() for o in extra_origins}:
        return True
    if host_header and parts.netloc.lower() == host_header.strip().lower():
        return True
    hostname = parts.hostname.lower()
    return hostname == "localhost" or is_loopback_address(hostname)


def token_matches(provided: object, expected: Optional[str]) -> bool:
    """Constant-time token comparison; never matches an empty token."""
    if not expected or not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def load_or_create_token(
    path: Path = DEFAULT_TOKEN_PATH,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """Return the configured token, creating a private random one on first use."""
    env_token = environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    path = Path(path)
    existing = _read_token(path)
    if existing:
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def _read_token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
