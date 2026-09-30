#!/usr/bin/env python3
"""
OpenFlight post-install self-test.

Checks what scripts/install.sh set up (UI build, service, udev rules,
serial permissions, UART, disk space) and then the radars themselves, reusing
the hardware checks from diagnose.py.

Usage:
    uv run python scripts/hardware-test/self_test.py
    uv run python scripts/hardware-test/self_test.py --no-interactive
    uv run python scripts/hardware-test/self_test.py --ops-port /dev/ttyAMA0
    uv run python scripts/hardware-test/self_test.py --software-only
"""

from __future__ import annotations

import argparse
import glob
import grp
import os
import shutil
import stat
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
LOG_DIR = Path.home() / "openflight_sessions"
MIN_FREE_BYTES = 1024**3


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
) -> CheckResult:
    """The boot-time kiosk service is installed and enabled."""

    def run():
        tool = systemctl or shutil.which("systemctl")
        if tool is None:
            return "skip", "systemd not available", ""
        result = subprocess.run(
            [tool, "is-enabled", unit_name], capture_output=True, text=True, check=False
        )
        state = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0:
            return "pass", f"{unit_name} {state}", ""
        return (
            "fail",
            f"{unit_name} {state or 'not installed'}",
            "Re-run scripts/install.sh, or: sudo systemctl enable openflight",
        )

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
) -> CheckResult:
    """Without dialout every radar open fails with 'Permission denied'."""

    def run():
        existing = existing_groups
        if existing is None:
            existing = {group.gr_name for group in grp.getgrall()}
        current = user_groups
        if current is None:
            current = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
        wanted = [g for g in ("dialout", "gpio", "i2c") if g in existing]
        missing = [g for g in wanted if g not in current]
        if not missing:
            return "pass", "member of " + ", ".join(wanted), ""
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
    state = DiagnosticState(ops243_port=args.ops_port)
    checks = software_checks()
    if not args.software_only:
        checks += hardware_checks(interactive=not args.no_interactive)

    print("OpenFlight Self-Test")
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
