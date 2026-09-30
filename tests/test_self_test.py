"""Tests for the post-install self-test script."""

import sys
from pathlib import Path

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
            user_groups={"pi", "gpio"}, existing_groups={"dialout", "gpio", "i2c"}
        )
        assert result.status == "fail"
        assert "dialout" in result.detail
        assert "usermod -aG dialout,i2c" in result.hint

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
