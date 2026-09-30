"""Server-side behaviour of --camera-archive-frames and --camera-frames-in-memory."""

import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server as server_module
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    SavedCameraCapture,
)
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture
from openflight.launch_monitor import Shot

# Module state main() assigns from argparse; restored so later test modules keep
# their import-time defaults.
MAIN_GLOBALS = (
    "ballistics_enabled",
    "air_density",
    "battery_provider",
    "profile_store",
    "ball_speed_correction_enabled",
    "ball_speed_correction_distance_ft",
    "ball_speed_correction_ball_above_radar_ft",
    "_VERTICAL_RADAR_GATE_BYPASS",
    "calculated_spin_enabled",
    "radar_auto_reconnect_enabled",
    "sim_connectors",
)

CAMERA_GLOBALS = (
    "camera_capture_runtime",
    "camera_capture_config",
    "camera_replay_manager",
    "camera_reference_ball_tracker",
    "camera_ball_flight_reference_tracker",
)

IWR_RUNTIME = SimpleNamespace(
    calibration=SimpleNamespace(
        tee_range_m=1.524,
        radar_height_m=0.15875,
        tee_ball_height_m=0.04,
    )
)

CAMERA_CONFIG = {
    "mount_height_m": 0.20955,
    "lateral_offset_m": 0.0,
    "horizontal_offset_deg": 0.0,
    "roll_correction_deg": 0.0,
    "mirror_horizontal": False,
    "width": 3,
    "height": 2,
}


def _capture(frame_count: int = 6, pre_trigger_count: int = 4) -> TriggeredCapture:
    frames = tuple(
        CameraFrame(
            image=np.full((2, 3), index * 7, dtype=np.uint8),
            sensor_timestamp_ns=index * 4_000_000,
            host_timestamp_ns=index * 4_000_000 + 100,
            exposure_us=1000,
            analogue_gain=4.0,
        )
        for index in range(frame_count)
    )
    return TriggeredCapture(
        frames=frames,
        pre_trigger_count=pre_trigger_count,
        trigger_host_timestamp_ns=12_000_000,
    )


def _memory_only_capture() -> SavedCameraCapture:
    runtime = CameraCaptureRuntime(
        output_dir="/nonexistent",
        settings=CameraCaptureSettings(
            frames_in_memory=True,
            archive_frames=False,
            auto_exposure=False,
        ),
    )
    return runtime._save_capture(1, 123.0, _capture())


class TestCliFlags:
    @pytest.mark.parametrize(
        ("extra_argv", "expected"),
        [
            ([], {"archive_frames": True, "frames_in_memory": False}),
            (["--camera-archive-frames"], {"archive_frames": True, "frames_in_memory": False}),
            (["--camera-frames-in-memory"], {"archive_frames": True, "frames_in_memory": True}),
            (
                ["--camera-frames-in-memory", "--no-camera-archive-frames"],
                {"archive_frames": False, "frames_in_memory": True},
            ),
        ],
    )
    def test_flags_reach_camera_init_and_default_to_disk_readback(
        self, monkeypatch, extra_argv, expected
    ):
        for name in MAIN_GLOBALS:
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--no-logging", "--camera-capture", *extra_argv],
        )
        received = {}

        def fake_init_camera_capture(**kwargs):
            received.update(kwargs)
            return False

        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kwargs: None)
        monkeypatch.setattr(server_module, "init_camera_capture", fake_init_camera_capture)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kwargs: None)
        monkeypatch.setattr(server_module.socketio, "run", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)

        server_module.main()

        assert {key: received[key] for key in expected} == expected

    def test_no_archive_without_in_memory_is_rejected(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--no-logging", "--camera-capture", "--no-camera-archive-frames"],
        )

        with pytest.raises(SystemExit):
            server_module.main()

        assert "--no-camera-archive-frames requires --camera-frames-in-memory" in (
            capsys.readouterr().err
        )


class TestInitCameraCapture:
    @pytest.mark.parametrize(
        ("archive_frames", "frames_in_memory"),
        [(True, False), (True, True), (False, True)],
    )
    def test_flags_reach_runtime_settings_and_config(
        self, monkeypatch, tmp_path, archive_frames, frames_in_memory
    ):
        from openflight.camera import capture_runtime

        for name in CAMERA_GLOBALS:
            monkeypatch.setattr(server_module, name, getattr(server_module, name))
        created = {}

        class FakeRuntime:
            def __init__(self, *, output_dir, settings, use_gpio_trigger):
                del output_dir, use_gpio_trigger
                self.settings = settings
                created["runtime"] = self

            def start(self):
                created["started"] = True

        monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", FakeRuntime)

        assert server_module.init_camera_capture(
            output_dir=tmp_path,
            gpio_pin=17,
            width=640,
            height=400,
            fps=300.0,
            pre_ms=150.0,
            post_ms=50.0,
            exposure_us=1000,
            gain=4.0,
            stream="raw",
            rotate_180=False,
            mirror_horizontal=False,
            roll_correction_deg=0.0,
            scaler_crop=None,
            mount_height_m=0.2,
            lateral_offset_m=0.0,
            horizontal_offset_deg=0.0,
            use_gpio_trigger=True,
            archive_frames=archive_frames,
            frames_in_memory=frames_in_memory,
        )

        settings = created["runtime"].settings
        assert created["started"] is True
        assert settings.archive_frames is archive_frames
        assert settings.frames_in_memory is frames_in_memory
        assert server_module.camera_capture_config["archive_frames"] is archive_frames
        assert server_module.camera_capture_config["frames_in_memory"] is frames_in_memory


class TestEnrichment:
    def test_in_memory_archive_equals_disk_readback(self, tmp_path):
        """The estimators see the same arrays whichever path the flag selects."""
        runtime = CameraCaptureRuntime(
            output_dir=tmp_path,
            settings=CameraCaptureSettings(frames_in_memory=True),
        )
        runtime._start_archive_writer()
        saved = runtime._save_capture(1, 123.0, _capture())
        assert saved.archive_ready.wait(timeout=2.0)
        runtime.stop()

        in_memory = server_module._load_camera_capture_archive(saved)
        from_disk = server_module._load_camera_capture_archive(
            SimpleNamespace(valid=True, path=saved.path)
        )

        assert in_memory is saved.archive
        assert set(in_memory) == set(from_disk)
        for name, array in from_disk.items():
            assert in_memory[name].dtype == array.dtype, name
            assert in_memory[name].shape == array.shape, name
            assert np.array_equal(in_memory[name], array), name

    def test_in_memory_capture_is_fused_without_touching_disk(self, monkeypatch):
        capture = _memory_only_capture()
        monkeypatch.setattr(
            np, "load", lambda *_a, **_k: pytest.fail("in-memory frames must not be re-read")
        )
        fused = []
        monkeypatch.setattr(
            server_module,
            "_fuse_camera_ball_flight",
            lambda _shot, _capture, archive: fused.append(archive),
        )
        monkeypatch.setattr(
            server_module,
            "_fuse_camera_club_delivery",
            lambda _shot, _capture, archive: fused.append(archive),
        )
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._fuse_camera_measurements(shot, capture)

        assert fused == [capture.archive, capture.archive]
        assert fused[0] is capture.archive

    def test_estimators_receive_in_memory_frames_for_unarchived_capture(self, monkeypatch):
        from openflight.camera import ball_flight, club_delivery

        capture = _memory_only_capture()
        assert capture.path is None
        calls = {}

        def fake_ball_flight(frames, timestamps_ns, **kwargs):
            calls["ball"] = (frames, timestamps_ns, kwargs["trigger_ns"])
            return ball_flight.CameraBallEstimate(status="accepted", confidence_tier="high")

        def fake_delivery(frames, host_timestamp_ns, **kwargs):
            calls["club"] = (frames, host_timestamp_ns, kwargs["trigger_index"])
            return club_delivery.ChainedDelivery(status="accepted")

        monkeypatch.setattr(ball_flight, "estimate_camera_ball_flight", fake_ball_flight)
        monkeypatch.setattr(club_delivery, "estimate_chained_delivery", fake_delivery)
        monkeypatch.setattr(server_module, "iwr6843_runtime", IWR_RUNTIME)
        monkeypatch.setattr(server_module, "camera_capture_config", CAMERA_CONFIG)
        monkeypatch.setattr(server_module, "camera_reference_ball_tracker", None)
        monkeypatch.setattr(server_module, "camera_ball_flight_reference_tracker", None)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club_speed_mph=90.0)

        server_module._fuse_camera_ball_flight(shot, capture, capture.archive)
        server_module._fuse_camera_club_delivery(shot, capture, capture.archive)

        assert calls["ball"][0] is capture.archive["frames"]
        assert calls["ball"][1] is capture.archive["host_timestamp_ns"]
        assert calls["ball"][2] == 12_000_000
        assert calls["club"][0] is capture.archive["frames"]
        assert calls["club"][2] == 3
        assert shot.experimental_fused_status == "accepted"
        assert shot.experimental_camera_horizontal_status.startswith("camera_")

    @pytest.mark.parametrize(
        ("capture", "expected"),
        [
            (None, "rejected_no_camera_capture"),
            (SimpleNamespace(valid=False, path="unused"), "rejected_no_camera_capture"),
            (SimpleNamespace(valid=True, path=None), "rejected_no_camera_capture"),
            (SimpleNamespace(valid=True, path="{tmp}"), "rejected_missing_camera_frames"),
        ],
    )
    def test_disk_readback_statuses_are_unchanged(self, monkeypatch, tmp_path, capture, expected):
        if capture is not None and capture.path == "{tmp}":
            capture = SimpleNamespace(valid=True, path=tmp_path)
        monkeypatch.setattr(server_module, "iwr6843_runtime", IWR_RUNTIME)
        monkeypatch.setattr(server_module, "camera_capture_config", CAMERA_CONFIG)
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club_speed_mph=90.0)

        server_module._fuse_camera_ball_flight(shot, capture)
        server_module._fuse_camera_club_delivery(shot, capture)

        assert shot.experimental_fused_status == expected
        assert shot.experimental_camera_horizontal_status.endswith(f":{expected}")

    def test_disk_readback_still_loads_npz_by_default(self, monkeypatch, tmp_path):
        np.savez(
            tmp_path / "frames.npz",
            frames=np.zeros((8, 4, 4), dtype=np.uint8),
            host_timestamp_ns=np.arange(8, dtype=np.int64),
            trigger_host_timestamp_ns=np.int64(3),
            pre_trigger_count=np.int32(4),
        )
        loads = []
        real_load = np.load

        def counted_load(path, *args, **kwargs):
            loads.append(path)
            return real_load(path, *args, **kwargs)

        monkeypatch.setattr(np, "load", counted_load)
        capture = SimpleNamespace(valid=True, path=tmp_path)

        archive = server_module._load_camera_capture_archive(capture)

        assert loads == [tmp_path / "frames.npz"]
        assert archive["frames"].shape == (8, 4, 4)


class TestReplayAvailability:
    def test_unarchived_capture_reports_replay_unavailable(self, monkeypatch, caplog):
        logged_errors = []

        class FakeManager:
            @staticmethod
            def register(*_args, **_kwargs):
                pytest.fail("nothing on disk to register")

        monkeypatch.setattr(server_module, "camera_replay_manager", FakeManager())
        monkeypatch.setattr(
            server_module,
            "log_session_error",
            lambda message, **kwargs: logged_errors.append(message),
        )
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
        capture = _memory_only_capture()

        with caplog.at_level("INFO", logger="openflight.server"):
            server_module._attach_camera_replay(shot, capture)

        assert shot.camera_replay is None
        assert server_module.shot_to_dict(shot)["camera_replay"] is None
        assert logged_errors == []
        assert "Replay unavailable" in caplog.text

    def test_pending_background_archive_is_passed_to_replay_manager(self, monkeypatch, tmp_path):
        calls = []

        class FakeManager:
            @staticmethod
            def register(path, metadata, **kwargs):
                calls.append((path, metadata, kwargs))
                return {"id": "replay-1"}

        monkeypatch.setattr(server_module, "camera_replay_manager", FakeManager())
        archive_ready = threading.Event()
        capture = SavedCameraCapture(
            sequence=1,
            trigger_timestamp=1.0,
            completed_timestamp=2.0,
            path=Path(tmp_path),
            metadata={"frame_count": 6, "pre_trigger_frames": 4},
            archive={"frames": np.zeros((6, 2, 3), dtype=np.uint8)},
            archive_ready=archive_ready,
        )
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._attach_camera_replay(shot, capture)

        assert calls == [(Path(tmp_path), capture.metadata, {"archive_ready": archive_ready})]
        assert shot.camera_replay == {"id": "replay-1"}

    def test_synchronous_archive_registers_exactly_as_before(self, monkeypatch, tmp_path):
        calls = []

        class FakeManager:
            @staticmethod
            def register(path, metadata):
                calls.append((path, metadata))
                return {"id": "replay-1"}

        monkeypatch.setattr(server_module, "camera_replay_manager", FakeManager())
        runtime = CameraCaptureRuntime(output_dir=tmp_path)
        capture = runtime._save_capture(1, 123.0, _capture())
        shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

        server_module._attach_camera_replay(shot, capture)

        assert calls == [(capture.path, capture.metadata)]
        assert shot.camera_replay == {"id": "replay-1"}


class TestIwr6843EstimatorFlags:
    """--iwr6843-estimator-process / --iwr6843-fast-angle-search default to today's path."""

    def test_defaults(self, monkeypatch):
        import inspect

        signature = inspect.signature(server_module.init_iwr6843)
        assert signature.parameters["estimator_process"].default is False
        assert signature.parameters["fast_angle_search"].default is False

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            ([], (False, False)),
            (["--iwr6843-estimator-process"], (True, False)),
            (["--iwr6843-fast-angle-search"], (False, True)),
        ],
    )
    def test_cli_flags_reach_init_iwr6843(self, monkeypatch, tmp_path, argv, expected):
        import sys

        seen = {}

        def fake_init(**kwargs):
            seen.update(kwargs)
            return False

        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--no-logging", "--iwr6843", *argv],
        )
        monkeypatch.setattr(server_module, "init_session_logger", lambda **_kw: None)
        monkeypatch.setattr(server_module, "init_iwr6843", fake_init)
        monkeypatch.setattr(server_module, "start_monitor", lambda **_kw: None)
        monkeypatch.setattr(server_module, "_cleanup_hardware_for_shutdown", lambda: None)
        monkeypatch.setattr(server_module, "air_density", server_module.air_density)
        with pytest.raises(SystemExit):
            server_module.main()
        assert (seen["estimator_process"], seen["fast_angle_search"]) == expected
