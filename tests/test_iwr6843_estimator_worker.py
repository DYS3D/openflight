"""IWR6843 estimators in a long-lived worker process, with inline fallback."""

from __future__ import annotations

import logging
import operator
import os
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from openflight.iwr6843 import Calibration
from openflight.iwr6843.estimator_worker import EstimatorWorker, EstimatorWorkerError
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.runtime import IWR6843Runtime
from tests.test_iwr6843_pipeline import RADAR_HEIGHT_M, range_snapshot_dump, synth_shot


@pytest.fixture(name="worker")
def _worker():
    worker = EstimatorWorker()
    yield worker
    worker.close()


def test_worker_runs_the_call_in_a_separate_long_lived_process(worker):
    assert worker.call(operator.add, 2, 3, timeout_s=30.0) == 5
    first_pid = worker.call(os.getpid, timeout_s=30.0)

    assert first_pid != os.getpid()
    assert worker.call(os.getpid, timeout_s=30.0) == first_pid
    assert worker.pid == first_pid


def test_worker_reraises_estimator_exceptions_and_stays_up(worker):
    pid = worker.call(os.getpid, timeout_s=30.0)

    with pytest.raises(ValueError):
        worker.call(int, "not a number", timeout_s=30.0)

    assert worker.call(os.getpid, timeout_s=30.0) == pid


def test_worker_timeout_kills_the_process_and_the_next_call_restarts_it(worker):
    pid = worker.call(os.getpid, timeout_s=30.0)

    start = time.monotonic()
    with pytest.raises(EstimatorWorkerError, match="no result within"):
        worker.call(time.sleep, 10.0, timeout_s=0.2)

    assert time.monotonic() - start < 5.0
    assert worker.pid is None
    assert worker.call(os.getpid, timeout_s=30.0) not in (None, pid)


def test_worker_crash_is_reported_as_a_worker_error(worker):
    with pytest.raises(EstimatorWorkerError):
        worker.call(os._exit, 3, timeout_s=30.0)  # pylint: disable=protected-access
    assert worker.pid is None


def test_unpicklable_request_is_a_worker_error(worker):
    with pytest.raises(EstimatorWorkerError, match="transport"):
        worker.call(lambda: 1, timeout_s=30.0)


def test_close_stops_the_process_and_refuses_later_calls(worker):
    worker.call(os.getpid, timeout_s=30.0)
    process = worker._process  # pylint: disable=protected-access

    worker.close()

    assert not process.is_alive()
    with pytest.raises(EstimatorWorkerError, match="closed"):
        worker.call(os.getpid, timeout_s=30.0)


def test_close_interrupts_a_call_in_flight(worker):
    worker.call(os.getpid, timeout_s=30.0)
    outcome = {}

    def busy_call():
        try:
            worker.call(time.sleep, 30.0, timeout_s=60.0)
        except EstimatorWorkerError as error:
            outcome["error"] = error

    caller = threading.Thread(target=busy_call)
    caller.start()
    time.sleep(0.2)
    start = time.monotonic()
    worker.close()
    caller.join(timeout=10.0)

    assert time.monotonic() - start < 10.0
    assert not caller.is_alive()
    assert isinstance(outcome.get("error"), EstimatorWorkerError)


class FakeCapture:
    valid = True
    path = None
    error = None
    trigger_timestamp = 1.0
    dump_duration_s = 5.3
    sequence = 1

    def __init__(self, raw: bytes):
        self.raw = raw


class FakeMonitor:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.stopped = False

    def capture_for_shot(self, _timestamp, timeout_s):
        del timeout_s
        return FakeCapture(self.raw)

    def stop(self):
        self.stopped = True


def _calibration() -> Calibration:
    cal = Calibration.identity()
    cal.tilt_rad = np.radians(10.4)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = RADAR_HEIGHT_M
    cal.meta["radar_height_m"] = RADAR_HEIGHT_M
    return cal


def _runtime(raw: bytes, **kwargs) -> IWR6843Runtime:
    return IWR6843Runtime(
        capture_monitor=FakeMonitor(raw),
        calibration=_calibration(),
        net_range_m=4.064,
        **kwargs,
    )


def _shot(runtime: IWR6843Runtime, ball_speed_mph: float):
    return runtime.process_shot(
        impact_timestamp=1.0,
        ball_speed_mph=ball_speed_mph,
        club="9i",
        club_speed_mph=75.0,
    )


def _three_tx_snapshot() -> bytes:
    """A capture on which recovery and club path both run past their gates."""
    return range_snapshot_dump(
        synth_shot(
            speed_ms=45.0,
            launch_deg=18.0,
            image_gain=0.35,
            noise=4.0,
            n_loops=10,
            n_tx=3,
            frame_period_us=4000,
        ),
        start_bin=20,
        n_bins=80,
    )


def _summary(result):
    return (
        result.measurement.to_dict(),
        result.club_path.to_dict() if result.club_path is not None else None,
    )


@pytest.mark.parametrize(
    "ball_speed_mph",
    [
        pytest.param(45.0 * 2.23694, id="ops_agrees"),
        # 20% OPS disagreement forces the OPS-guided recovery passes too.
        pytest.param(45.0 * 2.23694 * 1.2, id="ops_guided"),
    ],
)
def test_worker_process_results_equal_inline_results(ball_speed_mph):
    raw = _three_tx_snapshot()
    pooled = _runtime(raw)
    inline = _runtime(raw, estimator_process=False)
    try:
        pooled_result = _shot(pooled, ball_speed_mph)
        worker = pooled._estimator_worker  # pylint: disable=protected-access
        assert worker is not None and worker.pid not in (None, os.getpid())
        assert pooled.estimator_process
    finally:
        pooled.stop()

    inline_result = _shot(inline, ball_speed_mph)

    assert inline_result.measurement.angle_deg is not None
    assert inline_result.club_path is not None
    assert inline_result.club_path.status != "rejected_requires_three_tx"
    assert _summary(pooled_result) == _summary(inline_result)


def test_worker_timeout_falls_back_inline_for_this_and_later_shots(caplog):
    raw = synth_shot(speed_ms=45.0, launch_deg=18.0, image_gain=0.35, noise=4.0)
    runtime = _runtime(raw, estimator_timeout_s=0.001)
    inline = _runtime(raw, estimator_process=False)

    with caplog.at_level(logging.WARNING, logger="openflight.iwr6843.runtime"):
        result = _shot(runtime, 100.0)

    assert "running estimators inline" in caplog.text
    assert not runtime.estimator_process
    assert runtime._estimator_worker is None  # pylint: disable=protected-access
    assert _summary(result) == _summary(_shot(inline, 100.0))
    runtime.stop()


def test_worker_failure_falls_back_to_the_inline_estimators():
    class BrokenWorker:
        closed = False

        def call(self, *_args, **_kwargs):
            raise EstimatorWorkerError("could not start worker")

        def close(self):
            BrokenWorker.closed = True

    baseline = LCMFResult(status="accepted", angle_deg=20.0, track_speed_mph=100.0)
    runtime = _runtime(b"x" * 32)
    with (
        patch("openflight.iwr6843.runtime.EstimatorWorker", BrokenWorker),
        patch("openflight.iwr6843.runtime.prepare_lcmf_capture"),
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=baseline) as estimate,
    ):
        result = runtime.process_shot(impact_timestamp=1.0, ball_speed_mph=100.0, club="9i")

    assert result.measurement is baseline
    estimate.assert_called_once()
    assert BrokenWorker.closed
    assert not runtime.estimator_process


def test_stop_releases_the_worker_process_and_the_capture_monitor():
    raw = synth_shot(speed_ms=45.0, launch_deg=18.0, noise=4.0)
    runtime = _runtime(raw)
    worker = runtime._worker()  # pylint: disable=protected-access
    worker.call(os.getpid, timeout_s=30.0)
    process = worker._process  # pylint: disable=protected-access

    runtime.stop()

    assert runtime.capture_monitor.stopped
    assert not process.is_alive()
    assert runtime._estimator_worker is None  # pylint: disable=protected-access


def test_stop_without_any_shot_never_starts_a_worker():
    runtime = _runtime(b"")

    with patch("openflight.iwr6843.runtime.EstimatorWorker") as worker_class:
        runtime.stop()

    worker_class.assert_not_called()
    assert runtime.capture_monitor.stopped
