"""Tests for the post-install self-test script."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "hardware-test"))

import self_test  # noqa: E402


class TestUiBuild:
    def test_pass_when_built(self, tmp_path):
        (tmp_path / "ui" / "dist").mkdir(parents=True)
        (tmp_path / "ui" / "dist" / "index.html").write_text("<html>")
        assert self_test.check_ui_build(tmp_path).status == "pass"

    def test_fail_with_build_hint(self, tmp_path):
        result = self_test.check_ui_build(tmp_path)
        assert result.status == "fail"
        assert "npm run build" in result.hint


class TestService:
    def _fake_systemctl(self, tmp_path, stdout, code):
        tool = tmp_path / "systemctl"
        tool.write_text(f"#!/bin/sh\necho {stdout}\nexit {code}\n")
        tool.chmod(0o755)
        return str(tool)

    def test_enabled(self, tmp_path):
        result = self_test.check_service(systemctl=self._fake_systemctl(tmp_path, "enabled", 0))
        assert result.status == "pass"

    def test_disabled(self, tmp_path):
        result = self_test.check_service(systemctl=self._fake_systemctl(tmp_path, "disabled", 1))
        assert result.status == "fail"
        assert "disabled" in result.detail

    def test_no_systemd_is_skipped(self, monkeypatch):
        monkeypatch.setattr(self_test.shutil, "which", lambda _name: None)
        assert self_test.check_service().status == "skip"


class TestPermissions:
    def test_all_groups_present(self):
        result = self_test.check_serial_permissions(
            user_groups={"pi", "dialout", "gpio", "i2c"},
            existing_groups={"dialout", "gpio", "i2c"},
        )
        assert result.status == "pass"

    def test_missing_dialout_fails_with_usermod_hint(self):
        result = self_test.check_serial_permissions(
            user_groups={"pi", "gpio"},
            existing_groups={"dialout", "gpio", "i2c"},
            configured_groups={"gpio"},
        )
        assert result.status == "fail"
        assert "dialout" in result.detail
        assert "usermod -aG dialout,i2c" in result.hint

    def test_groups_added_by_the_installer_need_a_new_login_not_a_fix(self):
        """usermod -aG updates /etc/group, but the installer's own shell keeps its old groups."""
        result = self_test.check_serial_permissions(
            user_groups={"pi"},
            existing_groups={"dialout", "gpio", "i2c"},
            configured_groups={"dialout", "gpio", "i2c"},
        )
        assert result.status == "skip"
        assert "log out and back in" in result.detail

    def test_groups_the_os_lacks_are_not_required(self):
        result = self_test.check_serial_permissions(
            user_groups={"dialout"}, existing_groups={"dialout"}
        )
        assert result.status == "pass"


class TestFiles:
    def test_udev_rules(self, tmp_path):
        rules = tmp_path / "99-openflight.rules"
        assert self_test.check_udev_rules(rules).status == "fail"
        rules.write_text("x")
        assert self_test.check_udev_rules(rules).status == "pass"

    def test_disk_space_threshold(self, tmp_path):
        assert self_test.check_disk_space(tmp_path, min_free=1).status == "pass"
        assert self_test.check_disk_space(tmp_path, min_free=10**18).status == "fail"


class TestIwr6843:
    def test_flashed_board_passes(self):
        result = self_test.check_iwr6843_firmware(
            detect_port=lambda: "/dev/ttyUSB0", list_bridges=lambda: ["cp2105"]
        )
        assert result.status == "pass"
        assert "/dev/ttyUSB0" in result.detail

    def test_bridge_without_firmware_fails_with_flash_hint(self):
        result = self_test.check_iwr6843_firmware(
            detect_port=lambda: None, list_bridges=lambda: ["cp2105"]
        )
        assert result.status == "fail"
        assert "flash-iwr6843.sh" in result.hint

    def test_absent_board_is_optional(self):
        result = self_test.check_iwr6843_firmware(detect_port=lambda: None, list_bridges=list)
        assert result.status == "skip"

    def test_probe_errors_fail(self):
        def boom():
            raise PermissionError("denied")

        result = self_test.check_iwr6843_firmware(detect_port=boom, list_bridges=list)
        assert result.status == "fail"


class TestMain:
    def test_software_only_skips_radar_checks(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(
            self_test, "hardware_checks", lambda interactive: called.append(interactive) or []
        )
        monkeypatch.setattr(
            self_test,
            "software_checks",
            lambda: [lambda _s: self_test.CheckResult(name="ok", status="pass")],
        )
        assert self_test.main(["--software-only"]) == 0
        assert called == []
        assert "1 passed" in capsys.readouterr().out

    def test_any_failure_makes_exit_nonzero(self, monkeypatch):
        monkeypatch.setattr(
            self_test,
            "software_checks",
            lambda: [lambda _s: self_test.CheckResult(name="bad", status="fail")],
        )
        assert self_test.main(["--software-only"]) == 1

    def test_hardware_checks_reuse_diagnose(self):
        checks = self_test.hardware_checks(interactive=False)
        assert self_test.diagnose.check_ops243_connectivity in checks
        assert self_test.diagnose.check_uart_preflight in checks


class TestServiceState:
    def _systemctl(self, tmp_path, active):
        tool = tmp_path / "systemctl"
        tool.write_text(
            f'#!/bin/sh\n[ "$1" = is-enabled ] && {{ echo enabled; exit 0; }}\necho {active}\n'
        )
        tool.chmod(0o755)
        return str(tool)

    def test_failed_service_fails(self, tmp_path):
        result = self_test.check_service(systemctl=self._systemctl(tmp_path, "failed"))
        assert result.status == "fail"
        assert "journalctl" in result.hint

    def test_inactive_enabled_service_passes_with_its_state(self, tmp_path):
        result = self_test.check_service(systemctl=self._systemctl(tmp_path, "inactive"))
        assert result.status == "pass"
        assert "enabled, inactive" in result.detail

    def test_service_paused_by_the_doctor_reports_active(self, tmp_path):
        result = self_test.check_service(
            systemctl=self._systemctl(tmp_path, "inactive"), was_active=True
        )
        assert result.status == "pass"
        assert "active (paused" in result.detail


class TestUartConfig:
    def _boot(self, tmp_path, config, cmdline):
        (tmp_path / "config.txt").write_text(config)
        (tmp_path / "cmdline.txt").write_text(cmdline)
        return tmp_path

    def test_configured_uart_passes(self, tmp_path):
        boot = self._boot(
            tmp_path, "[all]\nenable_uart=1\ndtparam=uart0=on\n", "console=tty1 rootwait\n"
        )
        assert self_test.check_uart_config(True, boot).status == "pass"

    def test_serial_console_fails_for_a_uart_radar(self, tmp_path):
        boot = self._boot(
            tmp_path, "enable_uart=1\ndtparam=uart0=on\n", "console=serial0,115200 console=tty1\n"
        )
        result = self_test.check_uart_config(True, boot)
        assert result.status == "fail"
        assert "console=serial0,115200" in result.detail

    def test_missing_uart_is_only_a_skip_over_usb(self, tmp_path):
        boot = self._boot(tmp_path, "", "console=tty1\n")
        result = self_test.check_uart_config(False, boot)
        assert result.status == "skip"
        assert "enable_uart=1 missing" in result.detail
        assert self_test.check_uart_config(True, boot).status == "fail"

    def test_unreadable_boot_config(self, tmp_path):
        assert self_test.check_uart_config(True, tmp_path / "none").status == "fail"
        assert self_test.check_uart_config(False, tmp_path / "none").status == "skip"


class TestStablePorts:
    def test_usb_ops243_needs_its_symlink(self):
        present = {"/dev/openflight-ops243"}
        assert self_test.check_stable_ports(False, False, exists=present.__contains__).status == (
            "pass"
        )
        result = self_test.check_stable_ports(False, False, exists=lambda _p: False)
        assert result.status == "fail"
        assert "/dev/openflight-ops243" in result.detail

    def test_iwr6843_needs_cli_and_data_names(self):
        present = {"/dev/openflight-ops243", "/dev/openflight-iwr-cli"}
        result = self_test.check_stable_ports(True, False, exists=present.__contains__)
        assert result.status == "fail"
        assert result.detail == "missing /dev/openflight-iwr-data"

    def test_uart_ops243_has_no_symlink(self):
        assert self_test.check_stable_ports(False, True, exists=lambda _p: False).status == "skip"


def _result(status):
    return lambda _s: self_test.CheckResult(name=status, status=status, detail="d")


class TestDoctor:
    @pytest.mark.parametrize(
        ("statuses", "code"),
        [(["pass"], 0), (["pass", "skip"], 0), (["pass", "fail"], 1), (["fail"], 1)],
    )
    def test_exit_code_is_nonzero_on_any_fail(self, monkeypatch, capsys, statuses, code):
        checks = [_result(status) for status in statuses]
        monkeypatch.setattr(
            self_test, "doctor_plan", lambda _args: (self_test.DiagnosticState(), checks)
        )
        assert self_test.main(["--doctor"]) == code
        assert "OpenFlight Doctor" in capsys.readouterr().out

    def test_required_turns_not_found_into_fail(self):
        assert self_test._required(_result("skip"))(None).status == "fail"
        assert self_test._required(_result("pass"))(None).status == "pass"

    def test_missing_ops243_fails_the_doctor(self, monkeypatch):
        monkeypatch.setattr(self_test.diagnose, "check_ops243_connectivity", _result("skip"))
        checks = self_test.doctor_checks(
            expect_iwr=False, ops_on_uart=False, software_only=False, service_was_active=False
        )
        statuses = [check(self_test.DiagnosticState()).status for check in checks[5:6]]
        assert statuses == ["fail"]

    def test_iwr6843_is_required_only_when_expected(self, monkeypatch):
        monkeypatch.setattr(self_test, "check_iwr6843_firmware", lambda: _result("skip")(None))
        for expect, status in ((False, "skip"), (True, "fail")):
            checks = self_test.doctor_checks(
                expect_iwr=expect, ops_on_uart=False, software_only=False, service_was_active=False
            )
            assert checks[6](self_test.DiagnosticState()).status == status

    def test_software_only_touches_no_radar(self):
        checks = self_test.doctor_checks(
            expect_iwr=True, ops_on_uart=False, software_only=True, service_was_active=False
        )
        assert self_test.diagnose.check_uart_preflight not in checks
        assert len(checks) == 6

    def test_plan_follows_the_service_env_file(self, tmp_path, monkeypatch):
        seen = {}
        monkeypatch.setattr(self_test, "doctor_checks", lambda **kwargs: seen.update(kwargs) or [])
        env = tmp_path / "openflight"
        env.write_text('# x\nOPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0 --iwr6843"\n')

        state, _ = self_test.doctor_plan(
            self_test.parse_args(["--doctor"]), env_file=env, exists=lambda _p: False
        )

        assert state.ops243_port == "/dev/ttyAMA0"
        assert seen["expect_iwr"] is True
        assert seen["ops_on_uart"] is True

    def test_plan_prefers_the_stable_ops243_name(self, tmp_path):
        state, _ = self_test.doctor_plan(
            self_test.parse_args(["--doctor"]),
            env_file=tmp_path / "absent",
            exists={"/dev/openflight-ops243"}.__contains__,
        )
        assert state.ops243_port == "/dev/openflight-ops243"

    def test_explicit_port_wins(self, tmp_path):
        env = tmp_path / "openflight"
        env.write_text('OPENFLIGHT_ARGS="--radar-port /dev/ttyAMA0"\n')
        state, _ = self_test.doctor_plan(
            self_test.parse_args(["--doctor", "--ops-port", "/dev/ttyACM3"]),
            env_file=env,
            exists=lambda _p: True,
        )
        assert state.ops243_port == "/dev/ttyACM3"

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ('OPENFLIGHT_ARGS=""', []),
            (
                'OPENFLIGHT_ARGS="--iwr6843 --ops-port=/dev/ttyAMA0"',
                ["--iwr6843", "--ops-port=/dev/ttyAMA0"],
            ),
        ],
    )
    def test_service_args(self, tmp_path, line, expected):
        env = tmp_path / "openflight"
        env.write_text(line + "\n")
        assert self_test.service_args(env) == expected
        assert self_test._arg_value(expected, "--ops-port") == (
            "/dev/ttyAMA0" if expected else None
        )
