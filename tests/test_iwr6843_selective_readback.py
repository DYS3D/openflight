"""Selective readback publishes the ball measurement ahead of the full dump."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from openflight.iwr6843 import Calibration, estimate_lcmf_v1
from openflight.iwr6843.driver import ReadbackError
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.iwr6843.runtime import IWR6843Runtime
from tests.test_iwr6843_monitor import FakeButton
from tests.test_iwr6843_pipeline import RADAR_HEIGHT_M
from tests.test_iwr6843_readback import SHOTS, FrozenRadar, _timed_capture

IRON_MPH = SHOTS["iron"]["speed_ms"] * 2.23694


class ReadbackRadar(FrozenRadar):
    """Radar double with the readback firmware; the full dump waits to be released."""

    port = "/dev/fake-iwr6843"

    def __init__(self, raw: bytes, *, freeze_error: Exception | None = None):
        super().__init__(raw)
        self.freeze_error = freeze_error
        self.commands: list[str] = []
        self.release_dump = threading.Event()

    def send_config(self, _path: str) -> None:
        pass

    def freeze(self) -> None:
        self.commands.append("freeze")
        if self.freeze_error is not None:
            raise self.freeze_error

    def read_summary(self, scope: int) -> bytes:
        self.commands.append(f"summary {scope}")
        return super().read_summary(scope)

    def read_dump(self) -> bytes:
        self.commands.append("dump")
        assert self.release_dump.wait(timeout=5.0)
        return self.raw

    def stop_sensor(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture(name="cal")
def _cal():
    cal = Calibration.identity()
    cal.tilt_rad = np.radians(10.4)
    cal.tee_range_m = 1.5
    cal.tee_ball_height_m = RADAR_HEIGHT_M
    cal.meta["radar_height_m"] = RADAR_HEIGHT_M
    return cal


@pytest.fixture(name="started")
def _started(tmp_path):
    monitors = []

    def start(radar, **kwargs):
        config = tmp_path / "radar.cfg"
        config.write_text("sensorStart\n", encoding="utf-8")
        monitor = IWR6843CaptureMonitor(
            config_path=config,
            output_dir=tmp_path / "dumps",
            radar=radar,
            button_factory=FakeButton,
            selective_readback=True,
            readback_context=lambda: {"club": "9i", "net_range_m": None},
            **kwargs,
        )
        monitor.start()
        monitors.append((monitor, radar))
        return monitor

    yield start
    for monitor, radar in monitors:
        radar.release_dump.set()
        monitor.stop()


def test_ball_samples_are_published_before_the_full_dump_finishes(started):
    radar = ReadbackRadar(_timed_capture("iron"))
    monitor = started(radar)
    edge = time.time()

    assert monitor.notify_trigger(edge)
    early = monitor.capture_for_shot(edge, timeout_s=2.0)

    assert early is not None and early.valid
    assert early.raw is None and early.readback is not None
    assert early.full(0.05) is None, "the dump is still crossing the UART"
    radar.release_dump.set()
    full = early.full(2.0)
    assert full is not None and full.raw == radar.raw
    assert full.readback is early.readback
    assert radar.commands == ["freeze", "summary 0", "dump"]
    assert monitor.capture_for_shot(edge, timeout_s=0.05) is None, "one capture per shot"


def test_firmware_without_the_readback_commands_falls_back_to_the_full_dump(started):
    radar = ReadbackRadar(
        _timed_capture("iron"), freeze_error=RuntimeError("IWR6843 did not acknowledge")
    )
    radar.release_dump.set()
    monitor = started(radar)

    for _shot in range(2):
        time.sleep(0.15)  # the monitor ignores edges within 100 ms of the last
        edge = time.time()
        assert monitor.notify_trigger(edge)
        capture = monitor.capture_for_shot(edge, timeout_s=2.0)
        assert capture is not None and capture.raw == radar.raw and capture.readback is None

    assert radar.commands == ["freeze", "dump", "dump"], "readback is not retried every shot"


def test_a_failed_readback_leaves_the_shot_to_the_full_dump(started):
    radar = ReadbackRadar(_timed_capture("iron"))
    radar.read_windows = lambda _request, _nbytes: (_ for _ in ()).throw(
        ReadbackError("IWR6843 l3bins reply stalled")
    )
    radar.release_dump.set()
    monitor = started(radar)
    edge = time.time()

    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=2.0)

    assert capture is not None and capture.raw == radar.raw and capture.readback is None
    assert monitor.selective_readback, "one bad reply does not disable readback"


@pytest.fixture(name="make_runtime")
def _make_runtime():
    runtimes = []

    def make(monitor, cal) -> IWR6843Runtime:
        runtime = IWR6843Runtime(capture_monitor=monitor, calibration=cal, net_range_m=None)
        runtimes.append(runtime)
        return runtime

    yield make
    for runtime in runtimes:
        runtime._readback_worker.close()  # pylint: disable=protected-access


def test_readback_estimate_runs_outside_the_process_reading_the_dump(started, cal, make_runtime):
    radar = ReadbackRadar(_timed_capture("iron"))
    radar.release_dump.set()
    runtime = make_runtime(started(radar), cal)

    worker = runtime._readback_worker  # pylint: disable=protected-access
    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1") as inline_estimate:
        edge = time.time()
        assert runtime.capture_monitor.notify_trigger(edge)
        early = []
        runtime.process_shot(
            impact_timestamp=edge,
            ball_speed_mph=IRON_MPH,
            club="9i",
            on_ball_measurement=early.append,
        )

    assert early and worker.pid is not None
    inline_estimate.assert_not_called()


def test_runtime_reports_the_ball_measurement_before_the_dump_arrives(started, cal, make_runtime):
    radar = ReadbackRadar(_timed_capture("iron"))
    monitor = started(radar)
    runtime = make_runtime(monitor, cal)
    expected = estimate_lcmf_v1(radar.raw, cal, ball_speed_mph=IRON_MPH, club="9i")
    early = []

    def on_ball(result):
        early.append((result, radar.release_dump.is_set()))
        radar.release_dump.set()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(
        impact_timestamp=edge, ball_speed_mph=IRON_MPH, club="9i", on_ball_measurement=on_ball
    )

    assert len(early) == 1 and early[0][1] is False, "reported while the dump was pending"
    assert early[0][0].measurement.angle_deg == pytest.approx(expected.angle_deg, abs=1e-6)
    assert result.measurement is early[0][0].measurement
    assert result.capture.raw == radar.raw


def test_runtime_waits_for_the_full_dump_when_ops_disagrees_with_the_track(
    started, cal, make_runtime
):
    radar = ReadbackRadar(_timed_capture("iron"))
    radar.release_dump.set()
    monitor = started(radar)
    runtime = make_runtime(monitor, cal)
    early = []

    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(
        impact_timestamp=edge,
        ball_speed_mph=IRON_MPH * 1.5,
        club="9i",
        on_ball_measurement=early.append,
    )

    assert not early, "a track OPS disputes needs the recovery search on the whole ring"
    assert result.capture.raw == radar.raw
    assert result.measurement is not None


def test_runtime_keeps_the_readback_measurement_when_the_dump_fails(started, cal, make_runtime):
    radar = ReadbackRadar(_timed_capture("iron"))
    radar.read_dump = lambda: b"short"
    monitor = started(radar)
    runtime = make_runtime(monitor, cal)

    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=IRON_MPH, club="9i")

    assert result.measurement is not None and result.measurement.accepted
    assert result.capture.valid and result.capture.raw is None


def test_launch_is_reported_while_the_shot_pipeline_is_still_busy(started, cal, make_runtime):
    radar = ReadbackRadar(_timed_capture("iron"))
    monitor = started(radar)
    runtime = make_runtime(monitor, cal)
    early, late = [], []

    edge = time.time()
    assert monitor.notify_trigger(edge)
    # The pipeline has not asked for this shot yet (an earlier shot holds it).
    runtime.publish_early_ball_measurement(
        impact_timestamp=edge, ball_speed_mph=IRON_MPH, club="9i", on_ball_measurement=early.append
    )

    assert len(early) == 1 and not radar.release_dump.is_set()
    radar.release_dump.set()
    result = runtime.process_shot(
        impact_timestamp=edge, ball_speed_mph=IRON_MPH, club="9i", on_ball_measurement=late.append
    )
    assert not late, "the pipeline reuses the early answer instead of reporting it twice"
    assert result.measurement is early[0].measurement
    assert result.capture.raw == radar.raw


def test_looking_at_a_capture_leaves_it_and_older_ones_for_their_shots(started):
    radar = ReadbackRadar(_timed_capture("iron"))
    radar.release_dump.set()
    monitor = started(radar)
    first = time.time()
    assert monitor.notify_trigger(first)
    assert monitor.capture_for_shot(first, timeout_s=2.0, consume=False) is not None
    time.sleep(1.0)
    second = time.time()
    assert monitor.notify_trigger(second)

    assert monitor.capture_for_shot(second, timeout_s=2.0, consume=False) is not None

    assert monitor.capture_for_shot(first, timeout_s=0.5) is not None, (
        "older shot keeps its capture"
    )
    assert monitor.capture_for_shot(second, timeout_s=0.5) is not None


class ResumableRadar(ReadbackRadar):
    def resume(self) -> None:
        self.commands.append("resume")


def test_skip_switch_resumes_the_radar_when_the_readback_is_final(started, cal, make_runtime):
    radar = ResumableRadar(_timed_capture("iron"))
    monitor = started(radar, skip_full_dump=True)
    runtime = make_runtime(monitor, cal)

    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=IRON_MPH, club="9i")

    assert result.measurement is not None and result.measurement.accepted
    assert result.capture.raw is None and result.club_path is None
    assert radar.commands == ["freeze", "summary 0", "resume"], "the full dump was never read"


def test_skip_switch_still_reads_the_dump_when_the_readback_is_not_final(
    started, cal, make_runtime
):
    radar = ResumableRadar(_timed_capture("iron"))
    radar.release_dump.set()
    monitor = started(radar, skip_full_dump=True)
    runtime = make_runtime(monitor, cal)

    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=IRON_MPH * 1.5, club="9i")

    assert result.capture.raw == radar.raw
    assert radar.commands == ["freeze", "summary 0", "dump"]


def test_skip_switch_reads_the_dump_when_no_shot_claims_the_capture(started):
    radar = ResumableRadar(_timed_capture("iron"))
    radar.release_dump.set()
    monitor = started(radar, skip_full_dump=True, skip_decision_timeout_s=0.1)

    edge = time.time()
    assert monitor.notify_trigger(edge)
    early = monitor.capture_for_shot(edge, timeout_s=2.0)

    assert early.full(2.0).raw == radar.raw
    assert radar.commands == ["freeze", "summary 0", "dump"]


def test_a_trigger_during_a_capture_is_logged_as_ignored(started, caplog):
    radar = ReadbackRadar(_timed_capture("iron"))
    monitor = started(radar)
    edge = time.time()
    assert monitor.notify_trigger(edge)
    assert monitor.capture_for_shot(edge, timeout_s=2.0) is not None

    with caplog.at_level("INFO", logger="openflight.iwr6843.monitor"):
        assert not monitor.notify_trigger(edge + 5.0)

    assert "Trigger ignored: radar still busy with the shot 5.0s ago" in caplog.text
