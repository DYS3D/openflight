"""Optional access control for the web server (``--auth-required``).

Off by default, which keeps today's behaviour: any client on the network may
use the REST API and the socket. When enabled, clients that are not on the Pi
itself must present the device token, and browsers must come from an allowed
origin. Loopback (the kiosk browser) is always exempt so the touchscreen never
needs a keyboard.

The token lives in ``~/.config/openflight/token`` (mode 0600). The installer
creates and prints it (``python -m openflight.access print-token``); the server
also creates it on its first start with ``--auth-required``.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import logging
import os
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

TOKEN_ENV = "OPENFLIGHT_AUTH_TOKEN"
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "openflight" / "token"
TOKEN_HEADER = "X-OpenFlight-Token"
TOKEN_QUERY_PARAM = "token"


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


def token_matches(provided: object, expected: Optional[str]) -> bool:
    """Constant-time token comparison; never matches an empty token."""
    if not expected or not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def load_or_create_token(
    path: Path = DEFAULT_TOKEN_PATH,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """Return the device token, creating a private random one on first use.

    ``OPENFLIGHT_AUTH_TOKEN`` overrides the file (useful for tests and for a
    token managed elsewhere). The file is always written with mode 0600.
    """
    env_token = environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    path = Path(path)
    existing = _read_token(path)
    if existing:
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = _read_token(path)
        if existing:
            return existing
        # An empty file or a symlink: replace the entry itself, never its target.
        path.unlink()
        fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def _read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    if token and path.stat().st_mode & 0o077:
        logger.warning(
            "Token file %s is not private (mode looser than 0600); run chmod 600 on it", path
        )
    return token


def local_addresses() -> set[str]:
    """This machine's hostname, ``<hostname>.local`` and its IPv4 addresses."""
    names = {"localhost", "127.0.0.1", "[::1]"}
    hostname = socket.gethostname().lower()
    if hostname:
        names.add(hostname)
        names.add(f"{hostname}.local")
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            names.add(info[4][0])
    except (socket.gaierror, OSError):
        pass
    return names


_INTERFACE_IP_CACHE_S = 30.0
_interface_ip_lock = threading.Lock()
_interface_ip_cache: tuple[float, frozenset[str]] = (float("-inf"), frozenset())


def _primary_ipv4() -> Optional[str]:
    # Connecting a UDP socket sends nothing; it only asks the kernel which
    # interface address would route off-box. Pi OS resolves the hostname to
    # 127.0.1.1, so getaddrinfo never sees the LAN address.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return None


def interface_addresses(now: Optional[float] = None) -> frozenset[str]:
    """This machine's current routable IPv4 address, refreshed at most every 30 s."""
    global _interface_ip_cache  # pylint: disable=global-statement
    now = time.monotonic() if now is None else now
    with _interface_ip_lock:
        expires, addresses = _interface_ip_cache
        if now < expires:
            return addresses
        primary = _primary_ipv4()
        addresses = frozenset({primary} if primary and not is_loopback_address(primary) else ())
        _interface_ip_cache = (now + _INTERFACE_IP_CACHE_S, addresses)
        return addresses


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class AccessPolicy:
    """What ``--auth-required`` enforces. ``enabled=False`` means today's open server."""

    enabled: bool = False
    token: Optional[str] = None
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def disabled(cls) -> "AccessPolicy":
        return cls()

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        environ: Mapping[str, str] = os.environ,
    ) -> "AccessPolicy":
        if not getattr(args, "auth_required", False):
            # Today's open server: nothing is read or written.
            return cls.disabled()
        token_path = Path(getattr(args, "auth_token_file", DEFAULT_TOKEN_PATH)).expanduser()
        token = load_or_create_token(token_path, environ)
        hosts = set(local_addresses())
        for origin in getattr(args, "allowed_origin", None) or []:
            parts = urlsplit(origin if "://" in origin else f"http://{origin}")
            if parts.hostname:
                hosts.add(parts.hostname.lower())
        return cls(enabled=True, token=token, allowed_hosts=frozenset(hosts))

    def client_is_exempt(self, remote_addr: Optional[str]) -> bool:
        """The kiosk browser on the Pi itself never needs the token."""
        return is_loopback_address(remote_addr)

    def token_ok(self, provided: object) -> bool:
        return token_matches(provided, self.token)

    def origin_ok(self, origin: Optional[str], request_host: Optional[str] = None) -> bool:
        """Browsers must come from the Pi's own name/IP, localhost or a configured origin.

        Requests without an Origin header (curl, native apps, same-origin
        navigations) are not cross-origin and pass. An IP-literal origin is
        also accepted when it is the address the request was sent to
        (``request_host``) or one of the Pi's current interface addresses.
        """
        if not self.enabled or not origin:
            return True
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        hostname = parts.hostname.lower()
        if is_loopback_address(hostname) or hostname == "localhost":
            return True
        if hostname in self.allowed_hosts:
            return True
        if not _is_ip_literal(hostname):
            return False
        if request_host and urlsplit(f"//{request_host}").hostname == hostname:
            return True
        return hostname in interface_addresses()


def add_access_args(parser: argparse.ArgumentParser) -> None:
    """Register the ``--auth-required`` group on the server's parser."""
    group = parser.add_argument_group("Access control (off by default)")
    group.add_argument(
        "--auth-required",
        action="store_true",
        default=False,
        help=(
            "Require the device token from clients that are not on the Pi itself "
            "and only accept browsers from the Pi's own hostname/IP, localhost, or "
            "--allowed-origin. Off by default (today's open server)."
        ),
    )
    group.add_argument(
        "--auth-token-file",
        default=str(DEFAULT_TOKEN_PATH),
        help=(
            f"Where the device token lives (default: {DEFAULT_TOKEN_PATH}; created on first "
            f"start with mode 0600; ${TOKEN_ENV} overrides it)"
        ),
    )
    group.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="Extra browser origin or hostname to accept with --auth-required (repeatable)",
    )


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m openflight.access print-token``: create/show the device token."""
    parser = argparse.ArgumentParser(description="OpenFlight device token")
    parser.add_argument("command", choices=["print-token"])
    parser.add_argument("--token-file", default=str(DEFAULT_TOKEN_PATH))
    args = parser.parse_args(argv)
    sys.stdout.write(load_or_create_token(Path(args.token_file).expanduser()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
