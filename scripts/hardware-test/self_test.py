#!/usr/bin/env python3
"""
OpenFlight post-install self-test and doctor.

Checks what scripts/install.sh set up (UI build, service, udev rules,
serial permissions, UART, disk space) and then the radars themselves, reusing
the hardware checks from diagnose.py.

--doctor runs the PASS/FAIL checklist behind scripts/openflight-doctor.sh:
the OPS243 must be found and answer, and the IWR6843 must answer the
OpenFlight CLI when the service runs with --iwr6843 (or --expect-iwr6843).
The radar port and IWR6843 mode default to what /etc/default/openflight
gives the service.

Usage:
    uv run python scripts/hardware-test/self_test.py
    uv run python scripts/hardware-test/self_test.py --no-interactive
    uv run python scripts/hardware-test/self_test.py --ops-port /dev/ttyAMA0
    uv run python scripts/hardware-test/self_test.py --software-only
    uv run python scripts/hardware-test/self_test.py --doctor
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import glob
import grp
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import diagnose  # noqa: E402  pylint: disable=wrong-import-position
from diagnose import CheckResult, DiagnosticState  # noqa: E402

UDEV_RULES = Path("/etc/udev/rules.d/99-openflight.rules")
ENV_FILE = Path("/etc/default/openflight")
BOOT_DIR = Path("/boot/firmware")
LOG_DIR = Path.home() / "openflight_sessions"
MIN_FREE_BYTES = 1024**3
STABLE_OPS243 = "/dev/openflight-ops243"
STABLE_IWR_PORTS = ("/dev/openflight-iwr-cli", "/dev/openflight-iwr-data")
HARDWARE_GROUPS = ("dialout", "gpio", "i2c", "video")
_SERIAL_CONSOLE = re.compile(r"^console=(serial\d|ttyAMA\d+|ttyS\d+)(,.*)?$")


def _timed(name: str, check: Callable[[], tuple[str, str, str]]) -> CheckResult:
    start = time.time()
    status, detail, hint = check()
    return CheckResult(
        name=name, status=status, detail=detail, hint=hint, elapsed_s=time.time() - start
    )


def check_ui_build(project_dir: Path = PROJECT_DIR) -> CheckResult:
    """The kiosk serves ui/dist; without it the screen stays blank."""

    def run():
        index = project_dir / "ui" / "dist" / "index.html"
        if index.is_file():
            return "pass", str(index.relative_to(project_dir)), ""
        return "fail", "ui/dist/index.html missing", "cd ui && npm ci && npm run build"

    return _timed("UI build", run)


def check_service(
    unit_name: str = "openflight.service",
    systemctl: Optional[str] = None,
    was_active: bool = False,
) -> CheckResult:
    """The boot-time kiosk service is enabled and not failed.

    ``was_active`` is set when openflight-doctor.sh stopped a running service
    to free the radar ports, so its current state is not the real one.
    """

    def query(tool: str, verb: str) -> tuple[int, str]:
        result = subprocess.run(
            [tool, verb, unit_name], capture_output=True, text=True, check=False
        )
        return result.returncode, result.stdout.strip() or result.stderr.strip()

    def run():
        tool = systemctl or shutil.which("systemctl")
        if tool is None:
            return "skip", "systemd not available", ""
        code, enabled = query(tool, "is-enabled")
        if code != 0:
            return (
                "fail",
                f"{unit_name} {enabled or 'not installed'}",
                "Re-run scripts/install.sh, or: sudo systemctl enable openflight",
            )
        if was_active:
            return "pass", f"{unit_name} {enabled}, active (paused for these checks)", ""
        _code, active = query(tool, "is-active")
        if active == "failed":
            return (
                "fail",
                f"{unit_name} {enabled}, failed",
                "journalctl -u openflight -b; then sudo systemctl reset-failed openflight",
            )
        return "pass", f"{unit_name} {enabled}, {active or 'unknown'}", ""

    return _timed("Kiosk service", run)


def check_udev_rules(path: Path = UDEV_RULES) -> CheckResult:
    def run():
        if path.is_file():
            return "pass", str(path), ""
        return "fail", f"{path} missing", "Re-run scripts/install.sh"

    return _timed("udev rules", run)


def check_serial_permissions(
    user_groups: Optional[set[str]] = None,
    existing_groups: Optional[set[str]] = None,
    configured_groups: Optional[set[str]] = None,
) -> CheckResult:
    """Without dialout every radar open fails with 'Permission denied'.

    ``configured_groups`` is the user's membership in /etc/group. It differs
    from the running process's groups right after ``usermod -aG`` (as in the
    installer's own self-check) until the user logs in again.
    """

    def run():
        existing = existing_groups
        if existing is None:
            existing = {group.gr_name for group in grp.getgrall()}
        current = user_groups
        if current is None:
            current = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
        configured = configured_groups
        if configured is None:
            user = getpass.getuser()
            configured = {group.gr_name for group in grp.getgrall() if user in group.gr_mem}
        wanted = [g for g in HARDWARE_GROUPS if g in existing]
        missing = [g for g in wanted if g not in current]
        if not missing:
            return "pass", "member of " + ", ".join(wanted), ""
        if all(g in configured for g in missing):
            return (
                "skip",
                "added to " + ", ".join(missing) + "; takes effect after you log out and back in",
                "Reboot (or log out and back in), then run scripts/openflight-doctor.sh",
            )
        return (
            "fail",
            "missing groups: " + ", ".join(missing),
            f"sudo usermod -aG {','.join(missing)} $USER, then log out/in (or reboot)",
        )

    return _timed("Serial/GPIO permissions", run)


def check_disk_space(log_dir: Path = LOG_DIR, min_free: int = MIN_FREE_BYTES) -> CheckResult:
    def run():
        target = log_dir if log_dir.exists() else Path.home()
        free = shutil.disk_usage(target).free
        detail = f"{free / 1024**3:.1f} GB free at {target}"
        if free >= min_free:
            return "pass", detail, ""
        return "fail", detail, "Free space or lower --log-max-mb / --log-retention-days"

    return _timed("Disk space", run)


def check_iwr6843_firmware(
    detect_port: Optional[Callable[[], Optional[str]]] = None,
    list_bridges: Optional[Callable[[], list[str]]] = None,
) -> CheckResult:
    """A flashed IWR6843 answers the OpenFlight CLI; a bare CP2105 does not."""

    def run():
        detect = detect_port
        if detect is None:
            from openflight.iwr6843.driver import IWR6843Radar

            detect = IWR6843Radar.detect_port
        bridges = list_bridges or (lambda: sorted(glob.glob("/dev/serial/by-id/*CP2105*")))
        try:
            port = detect()
        except OSError as error:
            return "fail", f"probe failed: {error}", "Check the dialout group membership"
        if port:
            return "pass", f"OpenFlight firmware answering on {port}", ""
        if bridges():
            return (
                "fail",
                "IWR6843 USB bridge present but firmware not answering",
                "Set the EVM switches to run mode and press RESET, or flash with "
                "scripts/setup/flash-iwr6843.sh",
            )
        return "skip", "no IWR6843 connected (optional angle radar)", ""

    return _timed("IWR6843 firmware", run)


def check_uart_config(ops_on_uart: bool, boot_dir: Path = BOOT_DIR) -> CheckResult:
    """GPIO UART enabled in config.txt and no serial console on the kernel command line.

    Only an OPS243 wired to the 40-pin header needs this, so on USB a missing
    setting is reported as a skip rather than a failure.
    """

    def run():
        config = boot_dir / "config.txt"
        cmdline = boot_dir / "cmdline.txt"
        status_if_wrong = "fail" if ops_on_uart else "skip"
        try:
            config_lines = {line.strip() for line in config.read_text().splitlines()}
            cmdline_words = cmdline.read_text().split()
        except OSError as error:
            return status_if_wrong, f"cannot read boot config: {error}", ""
        problems = [
            f"{wanted} missing from {config}"
            for wanted in ("enable_uart=1", "dtparam=uart0=on")
            if wanted not in config_lines
        ]
        problems += [
            f"serial console {word} in {cmdline}"
            for word in cmdline_words
            if _SERIAL_CONSOLE.match(word)
        ]
        if not problems:
            return "pass", "UART0 enabled, no serial console", ""
        detail = "; ".join(problems)
        if not ops_on_uart:
            detail += " (only needed for an OPS243 on the GPIO header)"
        return status_if_wrong, detail, "Re-run scripts/install.sh, then reboot"

    return _timed("UART config", run)


def check_stable_ports(
    expect_iwr: bool,
    ops_on_uart: bool,
    exists: Callable[[str], bool] = os.path.exists,
) -> CheckResult:
    """The udev rules gave the connected radars their /dev/openflight-* names."""

    def run():
        wanted = [] if ops_on_uart else [STABLE_OPS243]
        if expect_iwr:
            wanted += list(STABLE_IWR_PORTS)
        if not wanted:
            return "skip", "OPS243 on the GPIO UART and no IWR6843 expected", ""
        missing = [path for path in wanted if not exists(path)]
        if not missing:
            return "pass", ", ".join(wanted), ""
        return (
            "fail",
            "missing " + ", ".join(missing),
            "Plug the radar in; if it is connected, re-run scripts/install.sh "
            "(udev rules) and replug the USB cable",
        )

    return _timed("Stable device names", run)


def _required(
    check: Callable[[DiagnosticState], CheckResult],
) -> Callable[[DiagnosticState], CheckResult]:
    """Treat a skip (hardware not found) as a failure."""

    def run(state: DiagnosticState) -> CheckResult:
        result = check(state)
        if result.status == "skip":
            return dataclasses.replace(result, status="fail")
        return result

    return run


def service_args(env_file: Path = ENV_FILE) -> list[str]:
    """Server arguments openflight.service gets from OPENFLIGHT_ARGS."""
    try:
        text = env_file.read_text()
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("OPENFLIGHT_ARGS="):
            value = shlex.split(line.split("=", 1)[1])
            return shlex.split(value[0]) if value else []
    return []


def _arg_value(args: list[str], *names: str) -> Optional[str]:
    for index, arg in enumerate(args):
        for name in names:
            if arg == name and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def doctor_checks(
    expect_iwr: bool, ops_on_uart: bool, software_only: bool, service_was_active: bool
) -> list[Callable[[DiagnosticState], CheckResult]]:
    """The openflight-doctor.sh checklist; every check here must pass or skip."""

    def iwr(_s: DiagnosticState) -> CheckResult:
        return check_iwr6843_firmware()

    checks: list[Callable[[DiagnosticState], CheckResult]] = [
        lambda _s: check_serial_permissions(),
        lambda _s: check_udev_rules(),
        lambda _s: check_uart_config(ops_on_uart),
        lambda _s: check_stable_ports(expect_iwr, ops_on_uart),
    ]
    if not software_only:
        checks += [
            diagnose.check_uart_preflight,
            _required(diagnose.check_ops243_connectivity),
            _required(iwr) if expect_iwr else iwr,
        ]
    checks += [
        lambda _s: check_service(was_active=service_was_active),
        lambda _s: check_disk_space(),
    ]
    return checks


def doctor_plan(
    args: argparse.Namespace,
    env_file: Path = ENV_FILE,
    exists: Callable[[str], bool] = os.path.exists,
) -> tuple[DiagnosticState, list[Callable[[DiagnosticState], CheckResult]]]:
    """Doctor checks and starting state, defaulting to the service's own settings."""
    configured = service_args(env_file)
    ops_port = args.ops_port or _arg_value(configured, "--radar-port", "--ops-port")
    if ops_port is None and exists(STABLE_OPS243):
        ops_port = STABLE_OPS243
    checks = doctor_checks(
        expect_iwr=args.expect_iwr6843 or "--iwr6843" in configured,
        ops_on_uart=diagnose.is_uart_port(ops_port),
        software_only=args.software_only,
        service_was_active=args.service_was_active,
    )
    return DiagnosticState(ops243_port=ops_port), checks


def software_checks() -> list[Callable[[DiagnosticState], CheckResult]]:
    return [
        lambda _s: check_ui_build(),
        lambda _s: check_service(),
        lambda _s: check_udev_rules(),
        lambda _s: check_serial_permissions(),
        lambda _s: check_disk_space(),
    ]


def hardware_checks(interactive: bool) -> list[Callable[[DiagnosticState], CheckResult]]:
    return [
        diagnose.check_uart_preflight,
        diagnose.check_ops243_connectivity,
        diagnose.check_ops243_rolling_buffer_persisted,
        lambda _s: check_iwr6843_firmware(),
        lambda s: diagnose.check_sound_trigger_end_to_end(s, interactive=interactive),
    ]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenFlight post-install self-test")
    parser.add_argument(
        "--no-interactive", action="store_true", help="Skip the clap-the-sound-trigger check"
    )
    parser.add_argument(
        "--software-only", action="store_true", help="Skip every check that touches a radar"
    )
    parser.add_argument(
        "--ops-port", default=None, help="OPS243 port; required for the GPIO UART (/dev/ttyAMA0)"
    )
    parser.add_argument(
        "--require-all", action="store_true", help="Treat skipped checks as failures"
    )
    parser.add_argument(
        "--doctor", action="store_true", help="PASS/FAIL checklist (scripts/openflight-doctor.sh)"
    )
    parser.add_argument(
        "--expect-iwr6843",
        action="store_true",
        help="With --doctor, require the IWR6843 (default: when the service uses --iwr6843)",
    )
    # Set by openflight-doctor.sh after it stops a running service.
    parser.add_argument("--service-was-active", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run_checks(
    checks: list[Callable[[DiagnosticState], CheckResult]], state: DiagnosticState
) -> list[CheckResult]:
    results = []
    for index, check in enumerate(checks, 1):
        result = check(state)
        results.append(result)
        diagnose.print_check_result(index, len(checks), result)
    return results


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.doctor:
        state, checks = doctor_plan(args)
    else:
        state = DiagnosticState(ops243_port=args.ops_port)
        checks = software_checks()
        if not args.software_only:
            checks += hardware_checks(interactive=not args.no_interactive)

    print("OpenFlight Doctor" if args.doctor else "OpenFlight Self-Test")
    print("=" * 40)
    try:
        results = run_checks(checks, state)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    finally:
        if state.ops243_radar is not None:
            try:
                state.ops243_radar.disconnect()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    print()
    print(diagnose.format_summary(results))
    healthy = diagnose.overall_status(results, require_all=args.require_all) == "HEALTHY"
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
