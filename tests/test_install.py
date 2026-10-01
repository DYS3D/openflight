"""Tests for scripts/install.sh, its udev rules, and the IWR6843 flash helper."""

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PROJECT_ROOT / "scripts" / "install.sh"
FLASHER = PROJECT_ROOT / "scripts" / "setup" / "flash-iwr6843.sh"
UDEV_RULES = PROJECT_ROOT / "scripts" / "setup" / "99-openflight.rules"
SERVICE = PROJECT_ROOT / "scripts" / "setup" / "openflight.service"

BOOT_CONFIG = "[pi5]\ndtoverlay=vc4-kms-v3d\n"
BOOT_CMDLINE = "console=serial0,115200 console=tty1 root=PARTUUID=abc rootwait\n"

INSTALL_STEPS = [
    "System packages",
    "uv (Python package manager)",
    "Python environment (uv sync)",
    "Node.js 22 and UI build",
    "GPIO UART on, serial console off, I2C on",
    "Hardware groups",
    "udev rules",
    "openflight systemd service",
    "Kiosk autostart",
    "Device token (for --auth-required)",
    "Software self-check",
]


def _call(script: Path, snippet: str, *args: str, env=None) -> subprocess.CompletedProcess[str]:
    """Source a script (without running main) and evaluate a snippet."""
    return subprocess.run(
        ["bash", "-c", f'source "$1"; shift; {snippet}', "test", str(script), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _fake_bin(tmp_path: Path, **scripts: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in scripts.items():
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\n" + body)
        tool.chmod(0o755)
    return bin_dir


def _dry_run(tmp_path: Path, *flags: str, desktop: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the installer with --dry-run as a normal user against temp boot files."""
    home = tmp_path / "home"
    home.mkdir()
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config.txt").write_text(BOOT_CONFIG)
    (boot / "cmdline.txt").write_text(BOOT_CMDLINE)
    # Pretend to be a normal user so the root guard passes in CI containers.
    tools = {"id": '[ "$1" = "-u" ] && echo 1000 || /usr/bin/id "$@"\n'}
    if desktop:
        tools["lightdm"] = "exit 0\n"
    bin_dir = _fake_bin(tmp_path, **tools)
    env = dict(
        os.environ,
        HOME=str(home),
        USER="pi",
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        OPENFLIGHT_BOOT_DIR=str(boot),
    )
    return subprocess.run(
        [str(INSTALLER), "--dry-run", *flags],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("script", [INSTALLER, FLASHER])
def test_scripts_have_valid_syntax_and_are_executable(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert os.access(script, os.X_OK)


def test_there_is_one_installer():
    assert not (PROJECT_ROOT / "scripts" / "setup" / "install-pi.sh").exists()


def test_installer_is_strict_and_logs_to_a_file():
    text = INSTALLER.read_text()
    assert "set -euo pipefail" in text
    assert 'exec > >(tee -a "$LOG_FILE") 2>&1' in text
    assert "$HOME/openflight-install.log" in text


class TestDryRun:
    def test_lists_every_step_and_changes_nothing(self, tmp_path):
        result = _dry_run(tmp_path, "--force")

        assert result.returncode == 0, result.stderr
        out = result.stdout
        for index, name in enumerate(INSTALL_STEPS, 1):
            assert f"[{index}/{len(INSTALL_STEPS)}] {name}" in out
        assert "[dry-run] sudo apt-get install -y git curl" in out
        for package in ("swig", "liblgpio-dev", "python3-dev", "ffmpeg"):
            assert f" {package} " in out
        assert "[dry-run] sudo raspi-config nonint do_boot_behaviour B4" in out or (
            "raspi-config not found" in result.stderr
        )
        assert "[dry-run] write /etc/udev/rules.d/99-openflight.rules" in out
        assert "openflight-ops243" in out
        assert "[dry-run] sudo systemctl enable openflight.service" in out
        assert "sync" in out and "npm" in out
        assert "self_test.py --software-only" in out
        assert "[dry-run] update " + str(tmp_path / "boot" / "config.txt") in out
        assert "+dtparam=uart0=on" in out
        # Nothing written: boot files intact, no backups, no log file in $HOME.
        assert (tmp_path / "boot" / "config.txt").read_text() == BOOT_CONFIG
        assert (tmp_path / "boot" / "cmdline.txt").read_text() == BOOT_CMDLINE
        assert sorted(p.name for p in (tmp_path / "boot").iterdir()) == [
            "cmdline.txt",
            "config.txt",
        ]
        assert list((tmp_path / "home").iterdir()) == []

    def test_refuses_other_platforms_without_force(self, tmp_path):
        result = _dry_run(tmp_path)
        assert result.returncode != 0
        assert "--force" in result.stderr
        assert "System packages" not in result.stdout

    def test_no_kiosk_skips_autologin(self, tmp_path):
        result = _dry_run(tmp_path, "--force", "--no-kiosk")
        assert result.returncode == 0, result.stderr
        assert "Kiosk autostart" in result.stdout
        assert "Skipped (--no-kiosk)" in result.stdout
        assert "do_boot_behaviour" not in result.stdout

    def test_lite_image_without_a_desktop_skips_the_kiosk(self, tmp_path):
        result = _dry_run(tmp_path, "--force", desktop=False)
        assert result.returncode == 0, result.stderr
        assert "No desktop found" in result.stderr
        assert "Skipped (no desktop)" in result.stdout
        assert "do_boot_behaviour" not in result.stdout
        assert "chromium" not in result.stdout

    def test_desktop_image_installs_chromium(self, tmp_path):
        result = _dry_run(tmp_path, "--force")
        assert re.search(r"apt-get install -y .*\bchromium", result.stdout)
        assert "No desktop found" not in result.stderr

    def test_service_waits_for_an_update_rollback_on_stop(self):
        assert "TimeoutStopSec=1800" in SERVICE.read_text()

    def test_service_starts_on_a_headless_boot(self):
        text = SERVICE.read_text()
        assert "WantedBy=multi-user.target" in text
        assert "After=network.target graphical.target" in text

    def test_optional_hardware_flags_reach_packages_and_service(self, tmp_path):
        result = _dry_run(
            tmp_path,
            "--force",
            "--with-iwr6843",
            "--with-camera",
            "--server-args",
            "--radar-port /dev/ttyAMA0",
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "python3-picamera2" in out
        assert "sync --extra camera" in out
        assert 'OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 --iwr6843 --camera-capture"' in out
        assert "flash-iwr6843.sh" in out

    def test_iwr6843_without_a_uart_radar_port_warns(self, tmp_path):
        result = _dry_run(tmp_path, "--force", "--with-iwr6843")
        assert result.returncode == 0, result.stderr
        assert "--radar-port /dev/ttyAMA0" in result.stderr

    def test_without_optional_flags_no_camera_packages(self, tmp_path):
        result = _dry_run(tmp_path, "--force")
        assert "picamera2" not in result.stdout
        assert "--extra camera" not in result.stdout

    def test_unknown_flag_fails(self, tmp_path):
        result = _dry_run(tmp_path, "--force", "--bogus")
        assert result.returncode != 0
        assert "Unknown option: --bogus" in result.stderr


class TestPlatform:
    def _problems(self, tmp_path, model: str, codename: str) -> list[str]:
        model_file = tmp_path / "model"
        model_file.write_bytes(model.encode() + b"\0")
        os_release = tmp_path / "os-release"
        os_release.write_text(f'ID=debian\nVERSION_CODENAME="{codename}"\n')
        result = _call(INSTALLER, 'platform_problems "$1" "$2"', str(model_file), str(os_release))
        return result.stdout.splitlines()

    def test_pi5_bookworm_passes(self, tmp_path):
        assert self._problems(tmp_path, "Raspberry Pi 5 Model B Rev 1.0", "bookworm") == []

    def test_other_board_and_os_are_reported(self, tmp_path):
        problems = self._problems(tmp_path, "Raspberry Pi 4 Model B Rev 1.4", "bullseye")
        assert len(problems) == 2
        assert "not a Raspberry Pi 5" in problems[0]
        assert "not Raspberry Pi OS Bookworm" in problems[1]

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

    def test_does_not_duplicate_existing_settings(self, tmp_path):
        config = tmp_path / "config.txt"
        config.write_text("enable_uart=1\n")
        _call(INSTALLER, 'update_uart_boot_config "$1"', str(config))
        assert config.read_text().count("enable_uart=1") == 1

    def _edit(self, config: Path, *, dry_run: bool = False):
        # sudo is a no-op stand-in so the edit runs against the temp file.
        return _call(
            INSTALLER,
            f'sudo() {{ "$@"; }}; DRY_RUN={"true" if dry_run else "false"}; '
            'edit_boot_file "$1" update_uart_boot_config_for 5',
            str(config),
        )

    def test_edit_keeps_one_timestamped_backup_and_is_idempotent(self, tmp_path):
        config = tmp_path / "config.txt"
        original = "[pi5]\ndtoverlay=vc4-kms-v3d\n"
        config.write_text(original)

        first = self._edit(config)
        second = self._edit(config)

        assert first.returncode == 0, first.stderr
        assert "already configured" in second.stdout
        backups = list(tmp_path.glob("config.txt.openflight-*.bak"))
        assert len(backups) == 1
        assert re.fullmatch(r"config\.txt\.openflight-\d{8}-\d{6}\.bak", backups[0].name)
        assert backups[0].read_text() == original
        assert config.read_text().count("dtparam=uart0=on") == 1

    def test_dry_run_edit_shows_diff_and_changes_nothing(self, tmp_path):
        config = tmp_path / "config.txt"
        config.write_text("[pi5]\n")

        result = self._edit(config, dry_run=True)

        assert result.returncode == 0, result.stderr
        assert "+dtparam=uart0=on" in result.stdout
        assert config.read_text() == "[pi5]\n"
        assert list(tmp_path.glob("*.bak")) == []

    def test_missing_boot_file_is_skipped(self, tmp_path):
        result = self._edit(tmp_path / "absent.txt")
        assert result.returncode == 0
        assert "not found" in result.stderr


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

    def test_installer_uses_raspi_config_for_uart_and_console(self):
        text = INSTALLER.read_text()
        assert "raspi-config nonint do_serial_hw 0" in text
        assert "raspi-config nonint do_serial_cons 1" in text


class TestServiceConfig:
    def test_env_file_carries_server_args(self):
        result = _call(
            INSTALLER,
            'parse_args "$@"; render_env_file',
            "--with-iwr6843",
            "--server-args",
            "--radar-port /dev/ttyAMA0 --altitude-ft 850",
        )
        assert result.returncode == 0, result.stderr
        assert 'OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 --altitude-ft 850 --iwr6843"' in (
            result.stdout
        )

    def test_rerun_keeps_earlier_server_args(self, tmp_path):
        """A later `--with-updates` re-run must not drop an earlier `--radar-port`."""
        env_file = tmp_path / "openflight"
        env_file.write_text('OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 --mock"\n')
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; shift; ENV_FILE="$1"; shift; parse_args "$@"; render_env_file',
                "test",
                str(INSTALLER),
                str(env_file),
                "--with-updates",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert 'OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 --mock --update-check"' in result.stdout

    @pytest.mark.parametrize(
        ("old", "new", "merged"),
        [
            ("--radar-port /dev/ttyAMA0", "--radar-port /dev/ttyUSB0", "--radar-port /dev/ttyUSB0"),
            ("--mock --update-check", "--update-check", "--mock --update-check"),
            ("--altitude-ft=850 --mock", "--altitude-ft=900", "--mock --altitude-ft=900"),
            ("", "--iwr6843", "--iwr6843"),
            ("--mock", "", "--mock"),
        ],
    )
    def test_merge_replaces_only_repeated_options(self, old, new, merged):
        result = _call(INSTALLER, 'merge_server_args "$1" "$2"', old, new)
        assert result.returncode == 0, result.stderr
        assert result.stdout == merged

    def test_updates_are_off_unless_asked_for(self):
        default = _call(INSTALLER, "parse_args; render_env_file")
        assert "--update-check" not in default.stdout
        result = _call(INSTALLER, 'parse_args "$@"; render_env_file', "--with-updates")
        assert result.returncode == 0, result.stderr
        assert 'OPENFLIGHT_ARGS="--update-check"' in result.stdout

    def test_env_file_is_empty_by_default(self):
        result = _call(INSTALLER, "parse_args; render_env_file")
        assert 'OPENFLIGHT_ARGS=""' in result.stdout

    @pytest.mark.parametrize("value", ['a"b', "a\\b", "a$b", "a`b"])
    def test_server_args_that_would_break_the_env_file_are_refused(self, value):
        result = _call(INSTALLER, 'parse_args "$@"', "--server-args", value)
        assert result.returncode != 0
        assert "must not contain" in result.stderr

    def test_service_reads_args_from_env_file(self):
        text = SERVICE.read_text()
        assert "EnvironmentFile=-/etc/default/openflight" in text
        assert "start-kiosk.sh $OPENFLIGHT_ARGS" in text

    def test_rendered_service_targets_user_and_checkout(self):
        result = _call(
            INSTALLER,
            'PROJECT_DIR="$1"; USER="$2"; of_render_unit "$3"',
            "/home/pi/openflight",
            "pi",
            str(SERVICE),
        )
        assert "User=pi" in result.stdout
        assert "ExecStart=/home/pi/openflight/scripts/start-kiosk.sh $OPENFLIGHT_ARGS" in (
            result.stdout
        )
        assert "coleman" not in result.stdout

    def test_unchanged_root_file_is_left_alone(self, tmp_path):
        dest = tmp_path / "unit"
        dest.write_text("same\n")
        result = _call(
            INSTALLER,
            'sudo() { echo "SUDO $*"; }; printf "same\\n" | install_root_file "$1" || echo "rc=$?"',
            str(dest),
        )
        assert "up to date" in result.stdout
        assert "rc=1" in result.stdout
        assert "SUDO" not in result.stdout


class TestUdevRules:
    def _rules(self) -> list[str]:
        return [
            line
            for line in UDEV_RULES.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

    def _rule_for(self, symlink: str) -> str:
        (rule,) = [line for line in self._rules() if f'SYMLINK+="{symlink}"' in line]
        return rule

    def test_every_rule_grants_dialout_access(self):
        rules = self._rules()
        assert len(rules) == 3
        for rule in rules:
            assert 'SUBSYSTEM=="tty"' in rule
            assert 'GROUP="dialout"' in rule and 'MODE="0660"' in rule

    def test_ops243_is_the_stm_cdc_acm_device(self):
        rule = self._rule_for("openflight-ops243")
        assert 'KERNEL=="ttyACM*"' in rule
        assert 'ATTRS{idVendor}=="0483"' in rule  # OPS243Radar.VENDOR_IDS

    @pytest.mark.parametrize(
        ("symlink", "interface"), [("openflight-iwr-cli", "00"), ("openflight-iwr-data", "01")]
    )
    def test_iwr6843_cp2105_interfaces(self, symlink, interface):
        rule = self._rule_for(symlink)
        assert 'ATTRS{idVendor}=="10c4"' in rule
        assert 'ATTRS{idProduct}=="ea70"' in rule
        assert f'ENV{{ID_USB_INTERFACE_NUM}}=="{interface}"' in rule

    def test_python_detection_uses_the_same_names(self):
        from openflight.iwr6843.driver import STABLE_CLI_PORT
        from openflight.ops243 import OPS243Radar

        assert OPS243Radar.STABLE_PORT == "/dev/" + "openflight-ops243"
        assert STABLE_CLI_PORT == "/dev/" + "openflight-iwr-cli"


def test_missing_groups_reports_only_existing_unjoined_groups():
    result = _call(
        INSTALLER,
        'id() { echo "pi adm dialout"; }; getent() { [ "$2" != video ]; }; missing_groups pi',
    )
    assert result.stdout.split() == ["gpio", "i2c"]


def test_hardware_groups_include_video():
    result = _call(INSTALLER, 'id() { echo "pi"; }; getent() { true; }; missing_groups pi')
    assert result.stdout.split() == ["dialout", "gpio", "i2c", "video"]


def test_piped_install_clones_then_runs_the_clone(tmp_path):
    script = INSTALLER.read_text()
    result = subprocess.run(
        ["bash", "-s", "--", "--dry-run", "--dir", str(tmp_path / "of"), "--branch", "dev"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "git clone --branch dev" in result.stdout
    assert str(tmp_path / "of") in result.stdout
    assert not (tmp_path / "of").exists()


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

    def test_prefers_the_udev_cli_name(self, tmp_path):
        by_id = tmp_path / "by-id"
        by_id.mkdir()
        (by_id / "usb-Silicon_Labs_CP2105_Dual_00ABC-if00-port0").write_text("")
        stable = tmp_path / "openflight-iwr-cli"
        stable.write_text("")
        result = _call(FLASHER, 'detect_cp2105_port "$1" "$2"', str(by_id), str(stable))
        assert result.stdout.strip() == str(stable)

    def test_default_stable_name_matches_the_udev_rules(self):
        assert 'STABLE_CLI_PORT="/dev/openflight-iwr-cli"' in FLASHER.read_text()

    def test_no_port_is_an_error(self, tmp_path):
        result = _call(FLASHER, 'detect_cp2105_port "$1" "$1/absent"', str(tmp_path))
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
