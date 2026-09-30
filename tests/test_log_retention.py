"""Tests for startup log retention."""

import os

from openflight.log_retention import prune_logs

DAY = 86_400
NOW = 1_800_000_000.0


def _write(path, size=10, age_days=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_disabled_limits_delete_nothing(tmp_path):
    old = _write(tmp_path / "session_20200101_000000_range.jsonl", age_days=999)
    assert prune_logs(tmp_path, max_age_days=0, max_total_mb=0, now=NOW) == []
    assert old.exists()


def test_missing_directory_is_a_no_op(tmp_path):
    assert prune_logs(tmp_path / "nope", max_age_days=1, max_total_mb=1, now=NOW) == []


def test_old_sessions_are_removed_with_their_sidecars(tmp_path):
    old = _write(tmp_path / "session_20200101_000000_range.jsonl", age_days=100)
    pushed = _write(tmp_path / "session_20200101_000000_range.jsonl.pushed", age_days=100)
    raw = _write(tmp_path / "radar_raw_20200101_000000.log", age_days=100)
    fresh = _write(tmp_path / "session_20260101_000000_range.jsonl", age_days=1)

    removed = prune_logs(tmp_path, max_age_days=90, max_total_mb=0, now=NOW)

    assert set(removed) == {old, pushed, raw}
    assert fresh.exists()


def test_capture_files_are_aged_out_and_empty_dirs_removed(tmp_path):
    dump = _write(tmp_path / "iwr6843" / "iwr6843_1_001.l3dump", age_days=120)
    frames = _write(tmp_path / "range" / "camera" / "camera_1_001" / "frames.npz", age_days=120)
    keep = _write(tmp_path / "range" / "camera" / "camera_2_001" / "frames.npz", age_days=2)

    removed = prune_logs(tmp_path, max_age_days=90, max_total_mb=0, now=NOW)

    assert set(removed) == {dump, frames}
    assert not frames.parent.exists()
    assert keep.exists()
    assert (tmp_path / "iwr6843").is_dir()


def test_unrelated_files_are_never_touched(tmp_path):
    notes = _write(tmp_path / "my_notes.txt", age_days=1000)
    other = _write(tmp_path / "photos" / "img.jpg", age_days=1000)
    prune_logs(tmp_path, max_age_days=1, max_total_mb=0.000001, now=NOW)
    assert notes.exists()
    assert other.exists()


def test_size_budget_removes_oldest_first(tmp_path):
    oldest = _write(tmp_path / "session_a_range.jsonl", size=600_000, age_days=3)
    middle = _write(tmp_path / "session_b_range.jsonl", size=600_000, age_days=2)
    newest = _write(tmp_path / "session_c_range.jsonl", size=600_000, age_days=1)

    removed = prune_logs(tmp_path, max_age_days=0, max_total_mb=1.2, now=NOW)

    assert removed == [oldest]
    assert middle.exists() and newest.exists()


def test_protected_sessions_survive_age_and_size_limits(tmp_path):
    pending = _write(tmp_path / "session_a_range.jsonl", size=2_000_000, age_days=400)
    uploaded = _write(tmp_path / "session_b_range.jsonl", size=2_000_000, age_days=300)

    removed = prune_logs(
        tmp_path,
        max_age_days=90,
        max_total_mb=1,
        protect=lambda path: path == pending,
        now=NOW,
    )

    assert removed == [uploaded]
    assert pending.exists()
