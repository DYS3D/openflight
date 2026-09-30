"""Coarse-to-fine LCMF launch-angle search against the exhaustive sweep."""

from __future__ import annotations

import time

import numpy as np
import pytest

from openflight.iwr6843 import Calibration, estimate_lcmf_v1, lcmf
from openflight.iwr6843.lcmf import (
    ANGLE_SEARCH_RANGE_DEG,
    FINE_ANGLE_STEP_DEG,
    _refine_grid,
    _search_angle,
    grid_curvature,
    prepare_lcmf_capture,
)
from tests.test_iwr6843_pipeline import RADAR_HEIGHT_M, range_snapshot_dump, synth_shot

# Coarse-to-fine interpolates on a 0.25 deg grid where the exhaustive sweep
# used 0.5 deg, so the two differ by sub-grid interpolation only. Synthetic
# shots below differ by at most ~0.11 deg; half the old grid step bounds it.
MAX_SEARCH_DISAGREEMENT_DEG = 0.25


@pytest.fixture(name="cal")
def _cal():
    cal = Calibration.identity()
    cal.tilt_rad = np.radians(10.4)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = RADAR_HEIGHT_M
    cal.meta["radar_height_m"] = RADAR_HEIGHT_M
    return cal


def _counting(objective):
    calls = []

    def objective_at(angle_deg: float) -> float:
        calls.append(angle_deg)
        return objective(angle_deg)

    return objective_at, calls


def test_exhaustive_search_evaluates_the_whole_grid_at_the_given_step():
    objective_at, calls = _counting(lambda angle: (angle - 17.3) ** 2)

    grid, objective = _search_angle(objective_at, 0.5)

    assert grid.tolist() == np.arange(-5.0, 45.25, 0.5).tolist()
    assert objective.tolist() == [(angle - 17.3) ** 2 for angle in grid]
    assert len(calls) == 101


def test_coarse_to_fine_finds_an_interior_minimum_on_the_fine_grid():
    objective_at, calls = _counting(lambda angle: (angle - 17.3) ** 2)

    grid, objective = _search_angle(objective_at, None)

    assert np.allclose(np.diff(grid), FINE_ANGLE_STEP_DEG)
    assert grid[0] < 17.3 < grid[-1]
    assert _refine_grid(grid, objective) == pytest.approx(17.3)
    assert grid_curvature(objective) is not None
    assert len(calls) == len(set(calls)), "no angle is evaluated twice"
    assert len(calls) <= 26 + 9


def test_coarse_to_fine_recenters_when_the_minimum_lies_beyond_the_fine_window():
    """A skewed objective puts the coarse winner >1 deg from the true minimum."""

    def skewed(angle: float) -> float:
        return 10.0 * (14.9 - angle) if angle < 14.9 else angle - 14.9

    assert int(np.argmin([skewed(a) for a in np.arange(-5.0, 46.0, 2.0)])) == 10  # 15 deg
    objective_at, _calls = _counting(skewed)

    grid, objective = _search_angle(objective_at, None)

    index = int(np.argmin(objective))
    assert 0 < index < len(grid) - 1
    assert abs(grid[index] - 14.9) <= FINE_ANGLE_STEP_DEG


def test_coarse_to_fine_reports_a_range_limit_minimum_as_off_the_grid():
    """An edge-pinned channel must still score None, as it does exhaustively."""
    objective_at, _calls = _counting(lambda angle: -angle)

    grid, objective = _search_angle(objective_at, None)

    assert grid[-1] == pytest.approx(ANGLE_SEARCH_RANGE_DEG[1])
    assert int(np.argmin(objective)) == len(grid) - 1
    assert grid_curvature(objective) is None
    assert _refine_grid(grid, objective) == pytest.approx(ANGLE_SEARCH_RANGE_DEG[1])


def _assert_equivalent(exhaustive, coarse_to_fine):
    assert coarse_to_fine.status == exhaustive.status
    assert coarse_to_fine.channels_used == exhaustive.channels_used
    assert coarse_to_fine.single_channel == exhaustive.single_channel
    assert set(coarse_to_fine.components_deg) == set(exhaustive.components_deg)
    for name, value in exhaustive.components_deg.items():
        assert coarse_to_fine.components_deg[name] == pytest.approx(
            value, abs=MAX_SEARCH_DISAGREEMENT_DEG
        ), name
    assert coarse_to_fine.angle_deg == pytest.approx(
        exhaustive.angle_deg, abs=MAX_SEARCH_DISAGREEMENT_DEG
    )


@pytest.mark.parametrize("launch_deg", [4.0, 13.0, 30.0])
def test_coarse_to_fine_matches_exhaustive_on_range_snapshot_shots(cal, launch_deg):
    raw = range_snapshot_dump(
        synth_shot(speed_ms=45.0, launch_deg=launch_deg, image_gain=0.35, noise=4.0, n_loops=10),
        start_bin=20,
        n_bins=80,
    )
    prepared = prepare_lcmf_capture(raw)
    kwargs = {"ball_speed_mph": 45.0 * 2.23694, "club": "9i", "prepared": prepared}

    exhaustive = estimate_lcmf_v1(raw, cal, **kwargs)  # production default
    coarse_to_fine = estimate_lcmf_v1(raw, cal, grid_step_deg=None, **kwargs)

    assert exhaustive.accepted
    _assert_equivalent(exhaustive, coarse_to_fine)


def test_coarse_to_fine_matches_exhaustive_and_does_less_work_on_raw_adc(cal, monkeypatch):
    """Timing sanity via work done, plus a lenient not-slower wall-clock check."""
    raw = synth_shot(speed_ms=45.0, launch_deg=18.0, image_gain=0.35, noise=4.0)
    prepared = prepare_lcmf_capture(raw)
    kwargs = {"ball_speed_mph": 45.0 * 2.23694, "club": "9i", "prepared": prepared}
    evaluations = []
    original = lcmf._frame_objective  # pylint: disable=protected-access

    def counting(*args, **inner):
        evaluations.append(1)
        return original(*args, **inner)

    monkeypatch.setattr(lcmf, "_frame_objective", counting)

    start = time.perf_counter()
    exhaustive = estimate_lcmf_v1(raw, cal, **kwargs)  # production default
    exhaustive_s = time.perf_counter() - start
    exhaustive_evaluations = len(evaluations)
    evaluations.clear()
    start = time.perf_counter()
    coarse_to_fine = estimate_lcmf_v1(raw, cal, grid_step_deg=None, **kwargs)
    coarse_to_fine_s = time.perf_counter() - start

    assert exhaustive.accepted
    _assert_equivalent(exhaustive, coarse_to_fine)
    # Five models x 101 angles exhaustively; at most 26 coarse + 9 fine plus
    # a re-center or two each for coarse-to-fine.
    assert exhaustive_evaluations == 5 * 101
    assert len(evaluations) <= 0.45 * exhaustive_evaluations
    assert coarse_to_fine_s < exhaustive_s
