"""Tests for scripts/setup/sync-to-pi.sh."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "setup" / "sync-to-pi.sh"


def _run_with_fake_rsync(tmp_path: Path) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "rsync"
    fake.write_text('#!/bin/bash\npwd > "$RSYNC_LOG"\nprintf "%s\\n" "$@" >> "$RSYNC_LOG"\n')
    fake.chmod(0o755)
    log = tmp_path / "rsync.log"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    subprocess.run(
        [str(SCRIPT)],
        cwd=elsewhere,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RSYNC_LOG": str(log)},
    )
    return log.read_text().splitlines()


def test_has_a_working_shebang():
    assert SCRIPT.read_text().startswith("#!/bin/bash\n")


def test_syncs_the_repo_root_even_from_another_directory(tmp_path):
    lines = _run_with_fake_rsync(tmp_path)

    assert Path(lines[0]).resolve() == REPO_ROOT
    assert "./" in lines[1:]


def test_keeps_the_pi_local_lock_files(tmp_path):
    arguments = _run_with_fake_rsync(tmp_path)[1:]

    assert "--exclude=uv.lock" in arguments
    assert "--exclude=uv.lock.update-backup" in arguments
