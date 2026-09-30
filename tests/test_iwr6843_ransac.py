"""The batched RANSAC in ``find_ball`` must reproduce the scalar loop exactly."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import tracking
from openflight.iwr6843.dump import is_range_snapshot, pack_dump, parse_dump
from openflight.iwr6843.shot import geometry_from_header
from openflight.iwr6843.tracking import (
    BALL_GATES_M,
    FAST_SUPPORT_FRAC,
    FAST_TRACK_MS,
    MAX_RADIAL_ACCEL,
    SPEED_BOUNDS_MS,
    BallTrack,
)
from tests.test_iwr6843_pipeline import _two_streak_cube, synth_shot


def _reference_find_ball(  # pylint: disable=too-many-locals
    mti,
    geo,
    *,
    iterations=2500,
    seed=1,
    max_range_m=None,
    min_ball_ms=FAST_TRACK_MS,
    gates_m=BALL_GATES_M,
    speed_bounds_ms=SPEED_BOUNDS_MS,
    time_window_s=None,
):
    """Verbatim copy of the scalar-loop ``find_ball`` this change replaced."""
    power = tracking.loop_power(mti)
    loops_idx, bins = tracking._detections(  # pylint: disable=protected-access
        power, geo, max_range_m=max_range_m, gates_m=gates_m
    )
    if loops_idx.size < 8:
        return None
    res = geo.range_res_m
    n_loops = geo.n_loops
    times = np.array([geo.loop_time(i // n_loops, i % n_loops) for i in loops_idx])
    if time_window_s is not None:
        keep = (times >= time_window_s[0]) & (times <= time_window_s[1])
        if keep.sum() < 8:
            return None
        times, bins, loops_idx = times[keep], bins[keep], loops_idx[keep]
    tol = 1.2 if geo.n_samples >= 128 else 0.8
    rng = np.random.default_rng(seed)
    best = None
    best_fast = None
    for _ in range(iterations):
        i, j = rng.choice(times.size, 2, replace=False)
        d_t = times[i] - times[j]
        if abs(d_t) < 3e-3:
            continue
        slope = (bins[i] - bins[j]) / d_t
        if not speed_bounds_ms[0] <= slope * res <= speed_bounds_ms[1]:
            continue
        icpt = bins[i] - slope * times[i]
        inliers = np.abs(bins - (slope * times + icpt)) < tol
        n_new = int(inliers.sum())
        if n_new < 8:
            continue
        beats_best = best is None or n_new > best[0]
        beats_fast = slope * res >= min_ball_ms and (best_fast is None or n_new > best_fast[0])
        if not (beats_best or beats_fast):
            continue
        design = np.vstack([times[inliers], np.ones(n_new)]).T
        (sl2, ic2), *_ = np.linalg.lstsq(design, bins[inliers], rcond=None)
        if not speed_bounds_ms[0] <= sl2 * res <= speed_bounds_ms[1]:
            continue
        resid = bins[inliers] - (sl2 * times[inliers] + ic2)
        rms = float(np.sqrt((resid**2).mean()))
        cand = (n_new, sl2, ic2, rms, float(times[inliers].min()), float(times[inliers].max()))
        if beats_best:
            best = cand
        if sl2 * res >= min_ball_ms and (best_fast is None or n_new > best_fast[0]):
            best_fast = cand
    if best is None:
        return None
    pick = best
    if (
        best_fast is not None
        and best[1] * res < min_ball_ms
        and best_fast[0] >= FAST_SUPPORT_FRAC * best[0]
    ):
        pick = best_fast
    n_inl, slope, icpt, rms, t_first, t_last = pick
    inl = np.abs(bins - (slope * times + icpt)) < tol
    quad = None
    if inl.sum() >= 10:
        q2, q1, q0 = np.polyfit(times[inl], bins[inl], 2)
        if abs(2.0 * q2 * res) < MAX_RADIAL_ACCEL:
            quad = (float(q2), float(q1), float(q0))
    span_s = t_last - t_first
    return BallTrack(
        speed_ms=slope * res,
        slope_bins=slope,
        intercept_bins=icpt,
        rms_bins=rms,
        n_inliers=n_inl,
        t_first=t_first,
        t_last=t_last,
        low_confidence=bool(rms >= 0.45 or span_s < 0.012),
        quad_bins=quad,
    )


def _mti_and_geometry(raw: bytes):
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    return mti, geo


def _noise_only() -> bytes:
    rng = np.random.default_rng(11)
    cube = (
        rng.standard_normal((12, 32, 4, 128)) + 1j * rng.standard_normal((12, 32, 4, 128))
    ) * 8.0
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)


CAPTURES = {
    "ball_18deg": lambda: synth_shot(speed_ms=45.0, launch_deg=18.0, noise=4.0),
    "ball_multipath": lambda: synth_shot(
        speed_ms=60.0, launch_deg=12.0, image_gain=0.35, noise=6.0, seed=4
    ),
    "ball_accelerating": lambda: synth_shot(speed_ms=40.0, accel_ms2=60.0, noise=4.0),
    "ball_noisy": lambda: synth_shot(speed_ms=35.0, launch_deg=25.0, noise=40.0, seed=7),
    "slow_theft": _two_streak_cube,
    "noise_only": _noise_only,
}


@pytest.mark.parametrize("capture", sorted(CAPTURES))
@pytest.mark.parametrize("seed", [1, 2, 99])
def test_batched_ransac_matches_the_scalar_loop_seed_for_seed(capture, seed):
    mti, geo = _mti_and_geometry(CAPTURES[capture]())

    assert tracking.find_ball(mti, geo, seed=seed) == _reference_find_ball(mti, geo, seed=seed)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"iterations": 0},
        {"iterations": 40},
        {"min_ball_ms": 40.0},
        {"max_range_m": 3.5},
        {"time_window_s": (0.0, 0.02)},
        {"gates_m": ((1.0, 2.5),), "speed_bounds_ms": (10.0, 40.0)},
    ],
)
def test_batched_ransac_matches_the_scalar_loop_for_every_search_option(kwargs):
    mti, geo = _mti_and_geometry(_two_streak_cube())

    assert tracking.find_ball(mti, geo, **kwargs) == _reference_find_ball(mti, geo, **kwargs)


def test_fixed_seed_is_deterministic_and_found_tracks_are_nontrivial():
    mti, geo = _mti_and_geometry(synth_shot(speed_ms=45.0, launch_deg=18.0, noise=4.0))

    first = tracking.find_ball(mti, geo, seed=5)
    second = tracking.find_ball(mti, geo, seed=5)

    assert first is not None
    assert first == second
    assert first.speed_ms == pytest.approx(45.0, rel=0.03)
