"""Tests for the one-command Raspberry Pi installer and IWR6843 flash helper."""

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "scripts" / "setup" / "install-pi.sh"
FLASHER = PROJECT_ROOT / "scripts" / "setup" / "flash-iwr6843.sh"
UDEV_RULES = PROJECT_ROOT / "scripts" / "setup" / "99-openflight.rules"
SERVICE = PROJECT_ROOT / "scripts" / "setup" / "openflight.service"


def _call(script: Path, snippet: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Source a script (without running main) and evaluate a snippet."""
    return subprocess.run(
        ["bash", "-c", f'source "$1"; shift; {snippet}', "test", str(script), *args],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("script", [INSTALLER, FLASHER])
def test_scripts_have_valid_syntax_and_are_executable(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK)


class TestUartBootConfig:
    def test_adds_uart_settings_once(self, tmp_path):
        config = tmp_path / "config.txt"
        config.write_text("[pi5]\ndtoverlay=vc4-kms-v3d\n")

        for _ in range(2):
            result = _call(INSTALLER, 'update_uart_boot_config "$1"', str(config))
            assert result.returncode == 0, result.stderr

        text = config.read_text()
        assert text.count("# OpenFlight UART") == 1
        assert text.count("enable_uart=1") == 1
        assert text.count("dtparam=uart0=on") == 1
        assert "\n[all]\n# OpenFlight UART" in text

    def test_pi4_moves_bluetooth_off_the_header_uart(self, tmp_path):
        config = tmp_path / "config.txt"
        config.write_text("")
        _call(INSTALLER, 'update_uart_boot_config "$1" 4', str(config))
        text = config.read_text()
        assert "dtoverlay=disable-bt" in text
        assert "dtparam=uart0=on" not in text

    @pytest.mark.parametrize(
        ("model", "generation"),
        [
            ("Raspberry Pi 5 Model B Rev 1.0", "5"),
            ("Raspberry Pi 4 Model B Rev 1.4", "4"),
            ("Some Other Board", "0"),
        ],
    )
    def test_pi_generation(self, tmp_path, model, generation):
        model_file = tmp_path / "model"
        model_file.write_bytes(model.encode() + b"\0")
        result = _call(INSTALLER, 'pi_generation "$1"', str(model_file))
        assert result.stdout.strip() == generation

    def test_does_not_duplicate_existing_settings(self, tmp_path):
        config = tmp_path / "config.txt"
        config.write_text("enable_uart=1\n")
        _call(INSTALLER, 'update_uart_boot_config "$1"', str(config))
        assert config.read_text().count("enable_uart=1") == 1


class TestSerialConsole:
    @pytest.mark.parametrize(
        "console", ["console=serial0,115200", "console=ttyAMA0,115200", "console=ttyS0"]
    )
    def test_strips_serial_consoles_only(self, tmp_path, console):
        cmdline = tmp_path / "cmdline.txt"
        cmdline.write_text(f"{console} console=tty1 root=PARTUUID=abc rootwait quiet\n")

        result = _call(INSTALLER, 'strip_serial_console "$1"', str(cmdline))

        assert result.returncode == 0, result.stderr
        assert cmdline.read_text() == "console=tty1 root=PARTUUID=abc rootwait quiet\n"

    def test_is_idempotent(self, tmp_path):
        cmdline = tmp_path / "cmdline.txt"
        cmdline.write_text("console=tty1 root=/dev/mmcblk0p2 rootwait\n")
        _call(INSTALLER, 'strip_serial_console "$1"', str(cmdline))
        assert cmdline.read_text() == "console=tty1 root=/dev/mmcblk0p2 rootwait\n"


class TestServiceConfig:
    def test_env_file_carries_site_settings(self):
        result = _call(
            INSTALLER,
            'parse_args "$@"; render_env_file',
            "--uart",
            "--altitude-ft",
            "5280",
            "--temperature-f",
            "70",
            "--server-args",
            "--iwr6843",
        )
        assert result.returncode == 0, result.stderr
        assert (
            'OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 '
            '--altitude-ft 5280 --temperature-f 70 --iwr6843"'
        ) in result.stdout

    def test_env_file_is_empty_by_default(self):
        result = _call(INSTALLER, 'parse_args; render_env_file')
        assert 'OPENFLIGHT_ARGS=""' in result.stdout

    @pytest.mark.parametrize("flag", ["--altitude-ft", "--temperature-f"])
    def test_non_numeric_site_values_are_refused(self, flag):
        result = _call(INSTALLER, 'parse_args "$@"', flag, "5280; rm -rf /")
        assert result.returncode != 0
        assert "must be a number" in result.stderr

    def test_service_reads_args_from_env_file(self):
        text = SERVICE.read_text()
        assert "EnvironmentFile=-/etc/default/openflight" in text
        assert "start-kiosk.sh $OPENFLIGHT_ARGS" in text

    def test_rendered_service_targets_user_and_checkout(self):
        result = _call(
            INSTALLER, 'render_service "$1" "$2" "$3"', "/home/pi/openflight", "pi", str(SERVICE)
        )
        assert "User=pi" in result.stdout
        assert "ExecStart=/home/pi/openflight/scripts/start-kiosk.sh" in result.stdout
        assert "coleman" not in result.stdout


def test_udev_rules_cover_both_radars():
    rules = UDEV_RULES.read_text()
    assert 'ATTRS{idVendor}=="0483"' in rules  # OPS243 VENDOR_IDS in ops243.py
    assert 'ATTRS{idProduct}=="ea70"' in rules
    assert 'SYMLINK+="ops243"' in rules and 'SYMLINK+="iwr6843"' in rules
    assert rules.count('GROUP="dialout"') == 2


def test_missing_groups_reports_only_existing_unjoined_groups():
    result = _call(
        INSTALLER,
        'id() { echo "pi adm dialout"; }; '
        'getent() { [ "$2" != video ]; }; '
        'missing_groups pi',
    )
    assert result.stdout.split() == ["gpio", "i2c"]


def test_dry_run_changes_nothing_and_lists_every_step(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # Pretend to be a normal user so the root guard passes in CI containers.
    (fake_bin / "id").write_text('#!/bin/sh\n[ "$1" = "-u" ] && echo 1000 || /usr/bin/id "$@"\n')
    (fake_bin / "id").chmod(0o755)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["USER"] = "pi"

    result = subprocess.run(
        [str(INSTALLER), "--dry-run", "--uart", "--skip-self-test"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "[dry-run] sudo apt-get install" in out
    assert "[dry-run] write /etc/udev/rules.d/99-openflight.rules" in out
    assert "[dry-run] write /etc/default/openflight" in out
    assert 'OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0"' in out
    assert "[dry-run] sudo systemctl enable openflight.service" in out
    assert "serial-getty@ttyAMA0.service" in out
    assert not (tmp_path / "openflight").exists()


class TestFlashHelper:
    def test_picks_newest_release_image(self, tmp_path):
        (tmp_path / "l3_dump_20260101.bin").write_bytes(b"MSTR")
        (tmp_path / "l3_dump_20260818.bin").write_bytes(b"MSTR")
        (tmp_path / "notes.txt").write_text("x")
        result = _call(FLASHER, 'latest_release_image "$1"', str(tmp_path))
        assert result.stdout.strip().endswith("l3_dump_20260818.bin")

    def test_repo_ships_a_release_image(self):
        result = _call(
            FLASHER, 'latest_release_image "$1"', str(PROJECT_ROOT / "firmware" / "releases")
        )
        assert result.stdout.strip().endswith(".bin")

    def test_detects_the_enhanced_cp2105_interface(self, tmp_path):
        for name in (
            "usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_00ABC-if01-port0",
            "usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_00ABC-if00-port0",
        ):
            (tmp_path / name).write_text("")
        result = _call(FLASHER, 'detect_cp2105_port "$1"', str(tmp_path))
        assert result.stdout.strip().endswith("-if00-port0")

    def test_no_port_is_an_error(self, tmp_path):
        result = _call(FLASHER, 'detect_cp2105_port "$1"', str(tmp_path))
        assert result.returncode != 0

    def test_dry_run_shows_the_flash_command(self):
        result = subprocess.run(
            [str(FLASHER), "--dry-run", "--port", "/dev/ttyUSB0"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "firmware/flash_iwr6843.py --port /dev/ttyUSB0" in result.stdout
        assert "firmware/releases/" in result.stdout

    def test_probe_does_not_need_an_image(self):
        result = subprocess.run(
            [str(FLASHER), "--dry-run", "--probe", "--port", "/dev/ttyUSB0"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "--probe" in result.stdout
        assert ".bin" not in result.stdout
