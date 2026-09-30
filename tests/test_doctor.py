"""scripts/openflight-doctor.sh is a thin wrapper around self_test.py --doctor."""

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCTOR = PROJECT_ROOT / "scripts" / "openflight-doctor.sh"


def _run(tmp_path: Path, *args: str, uv_exit: int = 0, service_active: bool = False):
    """Run the doctor with stub uv/systemctl/sudo that log their arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stubs = {
        "uv": f'echo "uv $*" >> "{log}"\nexit {uv_exit}\n',
        "systemctl": f'[ "$1" = is-active ] && exit {0 if service_active else 3}\nexit 0\n',
        "sudo": f'echo "sudo $*" >> "{log}"\n',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [str(DOCTOR), *args],
        env=dict(os.environ, HOME=str(home), PATH=f"{bin_dir}:{os.environ['PATH']}"),
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_is_executable_with_valid_syntax():
    subprocess.run(["bash", "-n", str(DOCTOR)], check=True)
    assert os.access(DOCTOR, os.X_OK)


@pytest.mark.parametrize("uv_exit", [0, 1])
def test_exit_code_is_the_checkers(tmp_path, uv_exit):
    result, calls = _run(tmp_path, uv_exit=uv_exit)
    assert result.returncode == uv_exit
    (call,) = calls
    assert "scripts/hardware-test/self_test.py --doctor --no-interactive" in call
    assert "--service-was-active" not in call


def test_running_service_is_paused_and_restarted(tmp_path):
    result, calls = _run(tmp_path, uv_exit=1, service_active=True)
    assert result.returncode == 1
    assert calls[0] == "sudo systemctl stop openflight"
    assert "--service-was-active" in calls[1]
    assert calls[2] == "sudo systemctl start openflight"


def test_software_only_leaves_the_service_running(tmp_path):
    result, calls = _run(tmp_path, "--software-only", service_active=True)
    assert result.returncode == 0
    assert not any(call.startswith("sudo") for call in calls)
    assert "--software-only" in calls[0]


def test_flags_map_to_the_checker(tmp_path):
    _result, calls = _run(tmp_path, "--with-iwr6843", "--ops-port", "/dev/ttyAMA0")
    assert "--expect-iwr6843 --ops-port /dev/ttyAMA0" in calls[0]


def test_unknown_flag_fails(tmp_path):
    result, calls = _run(tmp_path, "--bogus")
    assert result.returncode != 0
    assert calls == []
