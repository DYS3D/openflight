"""Tests for packaging metadata that affects setup/install behavior."""

import re
import subprocess
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


def _pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def _requirement_name(dependency: str) -> str:
    """Extract the distribution name from a PEP 508 dependency string."""
    return re.split(r"[<>=!~ \[;]", dependency, maxsplit=1)[0].strip()


def test_kld7_is_installed_by_default():
    """K-LD7 driver must be a base dependency so every install path includes it.

    The package is a tiny pure-Python wheel whose only requirement (pyserial) is
    already a base dependency, and the --kld7 runtime flag gates actual hardware
    use. Shipping it by default avoids the recurring "kld7 package not installed"
    failure on clean installs, since setup.sh, `uv sync`, and CI do not pull
    optional extras.
    """
    dependencies = _pyproject()["project"]["dependencies"]

    assert any(_requirement_name(dep) == "kld7" for dep in dependencies)


def test_camera_dependencies_are_not_installed_by_default():
    """Camera-only packages should not be part of the base install."""
    dependencies = _pyproject()["project"]["dependencies"]

    assert not any(_requirement_name(dep) == "opencv-python-headless" for dep in dependencies)
    assert not any(dep.startswith("trackers ") for dep in dependencies)
    assert not any(dep.startswith("supervision") for dep in dependencies)


def test_camera_extra_installs_portable_image_processing_dependency():
    """OpenCV is opt-in while Picamera2 remains an OS-managed Pi package."""
    camera_dependencies = _pyproject()["project"]["optional-dependencies"]["camera"]

    assert any(_requirement_name(dep) == "opencv-python-headless" for dep in camera_dependencies)
    assert not any(_requirement_name(dep) == "picamera2" for dep in camera_dependencies)


def test_eventlet_is_not_a_dependency():
    """The server runs Flask-SocketIO in threading mode; eventlet was never imported."""
    project = _pyproject()["project"]
    everything = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        everything.extend(extra)
    assert not any(_requirement_name(dep) == "eventlet" for dep in everything)


def _run_setup_python_check(tmp_path: Path, version: str) -> subprocess.CompletedProcess[str]:
    setup = Path("scripts/setup/setup.sh").read_text(encoding="utf-8")
    start = setup.index("# Check for Python")
    end = setup.index("# Check for Node.js")
    fake_python = tmp_path / "python3"
    fake_python.write_text(f"#!/bin/bash\necho {version}\n", encoding="utf-8")
    fake_python.chmod(0o755)
    script = f'log() {{ echo "$*"; }}\nerror() {{ echo "$*"; }}\nPATH={tmp_path}:$PATH\n{setup[start:end]}'
    return subprocess.run(["bash", "-c", script], check=False, capture_output=True, text=True)


def test_setup_python_check_matches_requires_python(tmp_path):
    minimum = _pyproject()["project"]["requires-python"].removeprefix(">=")
    major, minor = (int(part) for part in minimum.split("."))

    too_old = _run_setup_python_check(tmp_path, f"{major}.{minor - 1}")
    supported = _run_setup_python_check(tmp_path, minimum)

    assert too_old.returncode == 1
    assert f"Python {minimum}+ required" in too_old.stdout
    assert supported.returncode == 0
