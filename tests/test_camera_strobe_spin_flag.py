"""Server-side behaviour of the experimental --camera-strobe-spin flag."""

from __future__ import annotations

import sys
from datetime import datetime

import numpy as np
import pytest

from openflight import server as server_module
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture
from openflight.launch_monitor import Shot

from .test_camera_capture_flags import MAIN_GLOBALS
from .test_camera_spin_from_pair import blank, render_ball, rodrigues

FRAME_PERIOD_NS = 3_333_333


def _capture_from_images(images, trigger_index: int, host_delays_ns=None) -> object:
    delays = host_delays_ns or [0] * len(images)
    frames = tuple(
        CameraFrame(
            image=image,
            sensor_timestamp_ns=index * FRAME_PERIOD_NS,
            host_timestamp_ns=index * FRAME_PERIOD_NS + delays[index],
            exposure_us=40,
            analogue_gain=4.0,
        )
        for index, image in enumerate(images)
    )
    capture = TriggeredCapture(
        frames=frames,
        pre_trigger_count=trigger_index,
        trigger_host_timestamp_ns=trigger_index * FRAME_PERIOD_NS,
    )
    runtime = CameraCaptureRuntime(
        output_dir="/nonexistent",
        settings=CameraCaptureSettings(
            frames_in_memory=True, archive_frames=False, auto_exposure=False
        ),
    )
    return runtime._save_capture(1, 123.0, capture)


def _marked_ball_capture(host_delays_ns=None):
    pytest.importorskip("cv2")
    # 30 deg of backspin between the trigger frame and the next one.
    before = render_ball(blank(), 120.0, 100.0, 40.0)
    after = render_ball(blank(), 135.0, 88.0, 40.0, rodrigues((-1.0, 0.0, 0.0), 30.0))
    return _capture_from_images(
        [blank(), blank(), before, after, blank()], trigger_index=2, host_delays_ns=host_delays_ns
    )


def _run_main(monkeypatch, argv):
    for name in MAIN_GLOBALS + ("camera_strobe_spin_enabled",):
        monkeypatch.setattr(server_module, name, getattr(server_module, name))
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--no-logging", *argv])
    monkeypatch.setattr(server_module, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server_module, "init_camera_capture", lambda **_kwargs: False)
    monkeypatch.setattr(server_module, "start_monitor", lambda **_kwargs: None)
    monkeypatch.setattr(server_module.socketio, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
    server_module.main()


class TestCliFlag:
    def test_default_off(self, monkeypatch):
        _run_main(monkeypatch, ["--camera-capture"])
        assert server_module.camera_strobe_spin_enabled is False

    def test_flag_enables(self, monkeypatch):
        _run_main(monkeypatch, ["--camera-capture", "--camera-strobe-spin"])
        assert server_module.camera_strobe_spin_enabled is True

    def test_requires_camera_capture(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, ["--camera-strobe-spin"])
        assert "--camera-strobe-spin requires --camera-capture" in capsys.readouterr().err


class TestFusion:
    def test_off_by_default_leaves_shot_untouched(self, monkeypatch):
        capture = _marked_ball_capture()
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", False)
        monkeypatch.setattr(server_module, "_fuse_camera_ball_flight", lambda *_a: None)
        monkeypatch.setattr(server_module, "_fuse_camera_club_delivery", lambda *_a: None)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), spin_rpm=2600.0)

        server_module._fuse_camera_measurements(shot, capture)

        assert shot.camera_spin_status is None
        assert "camera_spin_rpm" not in shot.to_dict()
        assert "camera_spin_status" not in server_module.shot_to_dict(shot)

    def test_flag_attaches_camera_spin_without_touching_radar_spin(self, monkeypatch, caplog):
        capture = _marked_ball_capture()
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", True)
        monkeypatch.setattr(server_module, "_fuse_camera_ball_flight", lambda *_a: None)
        monkeypatch.setattr(server_module, "_fuse_camera_club_delivery", lambda *_a: None)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), spin_rpm=2600.0)

        with caplog.at_level("INFO", logger="openflight.server"):
            server_module._fuse_camera_measurements(shot, capture)

        assert shot.spin_rpm == 2600.0
        assert shot.camera_spin_status == "accepted"
        # 30 deg in one 300 fps frame period -> 1500 rpm.
        assert shot.camera_spin_rpm == pytest.approx(1500.0, rel=0.1)
        assert shot.camera_spin_axis_deg == pytest.approx(0.0, abs=6.0)
        assert shot.camera_spin_confidence > 0.5
        payload = shot.to_dict()
        assert payload["camera_spin_status"] == "accepted"
        assert payload["spin_rpm"] == 2600.0
        assert "Camera strobe spin: status=accepted" in caplog.text

    def test_spin_uses_sensor_frame_spacing_not_callback_jitter(self, monkeypatch):
        # Frame callbacks ran late for the trigger frame and on time for the
        # next, so host times are only ~1 ms apart while exposures are 3.3 ms.
        capture = _marked_ball_capture(host_delays_ns=[0, 0, 2_300_000, 0, 0])
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", True)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._fuse_camera_strobe_spin(shot, capture, capture.archive)

        assert shot.camera_spin_rpm == pytest.approx(1500.0, rel=0.1)

    def test_missing_frames_is_rejected(self, monkeypatch):
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", True)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._fuse_camera_strobe_spin(shot, None, None)

        assert shot.camera_spin_status == "rejected_no_camera_capture"
        assert shot.camera_spin_rpm is None

    def test_no_frame_after_trigger_is_rejected(self, monkeypatch):
        pytest.importorskip("cv2")
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", True)
        capture = _capture_from_images([blank(), blank()], trigger_index=1)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._fuse_camera_strobe_spin(shot, capture, capture.archive)

        assert shot.camera_spin_status == "rejected_insufficient_post_trigger_frames"

    def test_unmarked_ball_reports_no_mark(self, monkeypatch):
        pytest.importorskip("cv2")
        monkeypatch.setattr(server_module, "camera_strobe_spin_enabled", True)
        plain = render_ball(blank(), 120.0, 100.0, 40.0, mark_level=None)
        capture = _capture_from_images([plain, plain.copy()], trigger_index=0)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._fuse_camera_strobe_spin(shot, capture, capture.archive)

        assert shot.camera_spin_status == "no_mark"
        assert shot.camera_spin_rpm is None
        assert shot.camera_spin_confidence is None
        assert isinstance(capture.archive["frames"], np.ndarray)
