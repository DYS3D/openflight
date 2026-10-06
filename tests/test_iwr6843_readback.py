"""Selective L3 readback must give the estimator the full dump's answer."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import Calibration, estimate_lcmf_v1
from openflight.iwr6843.dump import pack_dump, parse_dump
from openflight.iwr6843.readback import (
    extract_windows,
    plan_readback,
    prepare_readback,
    summarize_capture,
    windows_nbytes,
)
from tests.test_iwr6843_pipeline import RADAR_HEIGHT_M, range_snapshot_dump, synth_shot

SHOTS = {
    "iron": {"speed_ms": 45.0, "launch_deg": 18.0, "image_gain": 0.35, "noise": 4.0},
    "driver": {"speed_ms": 62.0, "launch_deg": 12.0, "image_gain": 0.35, "noise": 6.0, "seed": 4},
    "mti_notch_speed": {"speed_ms": 53.9, "launch_deg": 15.0, "noise": 4.0, "seed": 2},
}


@pytest.fixture(name="cal")
def _cal():
    cal = Calibration.identity()
    cal.tilt_rad = np.radians(10.4)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = RADAR_HEIGHT_M
    cal.meta["radar_height_m"] = RADAR_HEIGHT_M
    return cal


def _capture(name: str) -> bytes:
    return range_snapshot_dump(synth_shot(n_loops=10, **SHOTS[name]), start_bin=20, n_bins=80)


def _readback(raw: bytes, club: str = "9i"):
    summary = summarize_capture(raw)
    windows = plan_readback(summary, club=club)
    assert windows is not None
    return summary, windows, prepare_readback(summary, windows, extract_windows(raw, windows))


@pytest.mark.parametrize("name", sorted(SHOTS))
def test_readback_reproduces_the_full_dump_estimate(cal, name):
    raw = _capture(name)
    speed_mph = SHOTS[name]["speed_ms"] * 2.23694
    full = estimate_lcmf_v1(raw, cal, ball_speed_mph=speed_mph, club="9i")

    _summary, _windows, prepared = _readback(raw)
    sparse = estimate_lcmf_v1(b"", cal, ball_speed_mph=speed_mph, club="9i", prepared=prepared)

    assert full.track_speed_mph is not None
    assert sparse.status == full.status
    assert sparse.angle_deg == pytest.approx(full.angle_deg, abs=1e-9)
    assert sparse.horizontal_deg == pytest.approx(full.horizontal_deg, abs=1e-9)
    assert sparse.track_speed_mph == pytest.approx(full.track_speed_mph, abs=1e-9)
    assert sparse.n_snapshots == full.n_snapshots


def test_readback_fetches_a_small_part_of_the_capture():
    raw = _capture("iron")
    metadata, cube = parse_dump(raw)

    summary, windows, _prepared = _readback(raw)

    assert len(windows) == metadata["n_frames"]
    assert (0, 0) in windows, "frames outside the ball's flight are not fetched"
    assert windows_nbytes(summary, windows) < 0.15 * cube.size * 4


def test_unfetched_samples_are_zero_in_the_rebuilt_capture():
    raw = _capture("iron")
    _summary, windows, prepared = _readback(raw)

    for frame, (low, count) in enumerate(windows):
        stored = prepared.full_cube[frame]
        assert not stored[:, :, :low].any()
        assert not stored[:, :, low + count :].any()


def test_capture_without_a_ball_plans_nothing():
    rng = np.random.default_rng(3)
    quiet = rng.normal(0.0, 4.0, (12, 20, 4, 64)) + 1j * rng.normal(0.0, 4.0, (12, 20, 4, 64))
    raw = range_snapshot_dump(
        pack_dump(quiet, n_tx=2, version=3, frame_period_us=6000), start_bin=20, n_bins=80
    )

    assert plan_readback(summarize_capture(raw), club="9i") is None


def test_raw_adc_capture_is_refused():
    with pytest.raises(ValueError, match="range-snapshot"):
        summarize_capture(synth_shot(speed_ms=45.0, launch_deg=18.0, n_loops=10))
