"""Tests for the high-speed camera trigger ring."""

import json
import threading
import time

import numpy as np
import pytest

from openflight.camera import capture_runtime
from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    ensure_picamera2_import_path,
    parse_scaler_crop,
    vertical_crop_limits,
)
from openflight.camera.triggered_buffer import (
    CameraFrame,
    TriggeredCapture,
    TriggeredFrameBuffer,
    timing_summary,
    unpack_r8_frame,
    unpack_yuv420_y_plane,
)


def make_frame(index: int, interval_ns: int = 4_000_000) -> CameraFrame:
    """Create a tiny deterministic camera frame."""
    return CameraFrame(
        image=np.full((2, 3), index, dtype=np.uint8),
        sensor_timestamp_ns=index * interval_ns,
        host_timestamp_ns=index * interval_ns + 100,
        exposure_us=1000,
        analogue_gain=4.0,
    )


def make_good_exposure_image() -> np.ndarray:
    """Create a frame with usable contrast in the impact-area ROI."""
    image = np.full((200, 320), 110, dtype=np.uint8)
    image[100:175, 90:230] = np.tile(
        np.linspace(45, 185, 140, dtype=np.uint8),
        (75, 1),
    )
    return image


def test_ring_freezes_latest_pre_frames_and_post_tail():
    ring = TriggeredFrameBuffer(pre_trigger_frames=3, post_trigger_frames=2)
    for index in range(5):
        ring.add_frame(make_frame(index))

    assert ring.trigger(host_timestamp_ns=19_000_000)
    ring.add_frame(make_frame(5))
    ring.add_frame(make_frame(6))

    capture = ring.wait_for_capture(timeout_s=0.01)
    assert capture is not None
    assert [int(frame.image[0, 0]) for frame in capture.frames] == [2, 3, 4, 5, 6]
    assert capture.pre_trigger_count == 3
    assert capture.post_trigger_count == 2
    assert capture.trigger_host_timestamp_ns == 19_000_000


def test_ring_exposes_latest_frame_during_capture():
    ring = TriggeredFrameBuffer(pre_trigger_frames=2, post_trigger_frames=2)
    ring.add_frame(make_frame(1))
    ring.add_frame(make_frame(2))

    assert ring.latest_frame is not None
    assert ring.latest_frame.image[0, 0] == 2
    assert ring.capture_busy is False

    assert ring.trigger()
    assert ring.capture_busy is True
    ring.add_frame(make_frame(3))

    assert ring.latest_frame is not None
    assert ring.latest_frame.image[0, 0] == 3
    ring.add_frame(make_frame(4))
    assert ring.capture_busy is False


def test_ring_rejects_overlapping_trigger():
    ring = TriggeredFrameBuffer(pre_trigger_frames=2, post_trigger_frames=2)
    ring.add_frame(make_frame(0))
    ring.add_frame(make_frame(1))
    assert ring.trigger()
    assert not ring.trigger()


def test_ring_rejects_trigger_until_pre_buffer_is_full():
    ring = TriggeredFrameBuffer(pre_trigger_frames=3, post_trigger_frames=1)
    ring.add_frame(make_frame(0))
    ring.add_frame(make_frame(1))
    assert not ring.trigger()

    ring.add_frame(make_frame(2))
    assert ring.trigger()


def test_wait_for_capture_wakes_on_completed_tail():
    ring = TriggeredFrameBuffer(pre_trigger_frames=1, post_trigger_frames=1)
    ring.add_frame(make_frame(0))
    ring.trigger()
    thread = threading.Thread(target=lambda: ring.add_frame(make_frame(1)))
    thread.start()
    capture = ring.wait_for_capture(timeout_s=0.1)
    thread.join()
    assert capture is not None


def test_pop_capture_consumes_ready_capture_without_blocking():
    ring = TriggeredFrameBuffer(pre_trigger_frames=1, post_trigger_frames=1)
    ring.add_frame(make_frame(0))
    assert ring.pop_capture() is None

    ring.trigger()
    ring.add_frame(make_frame(1))

    capture = ring.pop_capture()
    assert capture is not None
    assert ring.pop_capture() is None


def test_unpack_r8_from_pisp_high_bytes():
    pixels = np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint8)
    raw = np.zeros((2, 6), dtype=np.uint8)
    raw[:, 1::2] = pixels
    assert np.array_equal(unpack_r8_frame(raw, 3, 2, False), pixels)
    assert np.array_equal(unpack_r8_frame(raw, 3, 2, True), pixels[::-1, ::-1])


def test_unpack_r8_can_correct_horizontal_mirror_after_mount_rotation():
    pixels = np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint8)
    raw = np.zeros((2, 6), dtype=np.uint8)
    raw[:, 1::2] = pixels

    assert np.array_equal(
        unpack_r8_frame(raw, 3, 2, rotate_180=True, mirror_horizontal=True),
        pixels[::-1, :],
    )


def test_unpack_r8_rejects_short_frame():
    with pytest.raises(ValueError, match="unexpected raw frame"):
        unpack_r8_frame(np.zeros((1, 2), dtype=np.uint8), 3, 2, False)


def test_unpack_yuv420_y_plane_with_stride_and_chroma_rows():
    main = np.zeros((4, 5), dtype=np.uint8)
    main[:2, :3] = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)

    assert np.array_equal(
        unpack_yuv420_y_plane(main, 3, 2, False),
        np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8),
    )
    assert np.array_equal(
        unpack_yuv420_y_plane(main, 3, 2, True),
        np.array([[6, 5, 4], [3, 2, 1]], dtype=np.uint8),
    )


def test_unpack_yuv420_y_plane_rejects_short_frame():
    with pytest.raises(ValueError, match="unexpected YUV420 frame"):
        unpack_yuv420_y_plane(np.zeros((1, 2), dtype=np.uint8), 3, 2, False)


def test_parse_scaler_crop():
    assert parse_scaler_crop("256,160,768,480") == (256, 160, 768, 480)
    assert parse_scaler_crop(None) is None
    with pytest.raises(ValueError, match="X,Y,W,H"):
        parse_scaler_crop("1,2,3")
    with pytest.raises(ValueError, match="positive"):
        parse_scaler_crop("1,2,0,4")


def test_ensure_picamera2_import_path_adds_pi_dist_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_runtime, "RASPBERRY_PI_DIST_PACKAGES", tmp_path)
    monkeypatch.setattr(capture_runtime.sys, "path", [])

    assert ensure_picamera2_import_path()
    assert capture_runtime.sys.path == [str(tmp_path)]
    assert ensure_picamera2_import_path()
    assert capture_runtime.sys.path == [str(tmp_path)]


def test_timing_summary_reports_fps_and_gap():
    frames = [make_frame(index) for index in range(5)]
    summary = timing_summary(frames)
    assert summary["delivered_fps"] == pytest.approx(250.0)
    assert summary["median_interval_ms"] == pytest.approx(4.0)
    assert summary["gap_count"] == 0

    frames[-1] = make_frame(6)
    summary = timing_summary(frames)
    assert summary["gap_count"] == 1


def test_capture_persistence_uses_fast_uncompressed_npz(tmp_path, monkeypatch):
    """The live callback must not wait on expensive ZIP compression."""
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    capture = TriggeredCapture(
        frames=tuple(make_frame(index) for index in range(3)),
        pre_trigger_count=2,
        trigger_host_timestamp_ns=4_000_000,
    )
    compressed_calls = []
    original_savez = np.savez

    monkeypatch.setattr(
        capture_runtime.np,
        "savez_compressed",
        lambda *_args, **_kwargs: compressed_calls.append(True),
    )
    monkeypatch.setattr(capture_runtime.np, "savez", original_savez)

    saved = runtime._save_capture(1, 123.0, capture)

    assert compressed_calls == []
    with np.load(saved.path / "frames.npz") as archive:
        assert archive["frames"].shape == (3, 2, 3)
    assert saved.metadata["storage_format"] == "npz_uncompressed"


def make_capture(frame_count: int = 4, pre_trigger_count: int = 3) -> TriggeredCapture:
    """Create a small capture whose frames carry distinct pixel values."""
    return TriggeredCapture(
        frames=tuple(make_frame(index) for index in range(frame_count)),
        pre_trigger_count=pre_trigger_count,
        trigger_host_timestamp_ns=8_000_000,
    )


def test_default_settings_archive_synchronously_for_disk_readback(tmp_path):
    """Today's path: frames.npz is complete before the capture is published."""
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._start_archive_writer()

    saved = runtime._save_capture(1, 123.0, make_capture())

    assert runtime.defers_archive_writes is False
    assert runtime._archive_worker is None
    assert saved.archive is None
    assert saved.archive_ready is None
    assert (saved.path / "frames.npz").is_file()
    assert (saved.path / "trigger.pgm").is_file()
    assert isinstance(saved.metadata["npz_bytes"], int)
    assert saved.metadata["save_time_ms"] > 0
    on_disk = json.loads((saved.path / "metadata.json").read_text())
    assert on_disk["npz_bytes"] == saved.metadata["npz_bytes"]
    assert on_disk["settings"]["archive_frames"] is True
    assert on_disk["settings"]["frames_in_memory"] is False


def test_frames_in_memory_publishes_capture_before_background_archive(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(frames_in_memory=True),
    )
    release_write = threading.Event()
    real_write = capture_runtime._write_archive_files

    def blocked_write(*args, **kwargs):
        assert release_write.wait(timeout=2.0)
        real_write(*args, **kwargs)

    monkeypatch.setattr(capture_runtime, "_write_archive_files", blocked_write)
    runtime._start_archive_writer()

    saved = runtime._save_capture(1, 123.0, make_capture())

    assert runtime.defers_archive_writes is True
    assert saved.valid
    assert saved.archive is not None
    assert saved.archive["frames"].shape == (4, 2, 3)
    assert saved.path is not None and saved.path.is_dir()
    assert not saved.archive_ready.is_set()
    assert not (saved.path / "frames.npz").exists()
    assert saved.metadata["capture_path"] == str(saved.path)
    assert saved.metadata["storage_format"] == "npz_uncompressed"
    assert saved.metadata["npz_bytes"] is None

    release_write.set()
    assert saved.archive_ready.wait(timeout=2.0)
    assert (saved.path / "frames.npz").is_file()
    assert (saved.path / "first.pgm").is_file()
    on_disk = json.loads((saved.path / "metadata.json").read_text())
    assert on_disk["npz_bytes"] == (saved.path / "frames.npz").stat().st_size
    assert on_disk["save_time_ms"] >= saved.metadata["save_time_ms"]
    runtime.stop()


def test_in_memory_archive_is_identical_to_loaded_npz(tmp_path):
    """Estimators must see exactly what a disk round trip would give them."""
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(frames_in_memory=True),
    )
    runtime._start_archive_writer()

    saved = runtime._save_capture(2, 456.5, make_capture(frame_count=5, pre_trigger_count=2))
    assert saved.archive_ready.wait(timeout=2.0)
    runtime.stop()

    with np.load(saved.path / "frames.npz") as loaded:
        assert set(loaded.files) == set(saved.archive)
        for name in loaded.files:
            from_disk = loaded[name]
            in_memory = saved.archive[name]
            assert isinstance(in_memory, np.ndarray), name
            assert in_memory.dtype == from_disk.dtype, name
            assert in_memory.shape == from_disk.shape, name
            assert np.array_equal(in_memory, from_disk), name
    assert int(saved.archive["pre_trigger_count"]) == 2
    assert int(saved.archive["trigger_host_timestamp_ns"]) == 8_000_000
    assert float(saved.archive["trigger_epoch_timestamp"]) == 456.5


def test_archive_frames_off_keeps_capture_in_memory_only(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(frames_in_memory=True, archive_frames=False),
    )
    runtime._start_archive_writer()

    saved = runtime._save_capture(1, 123.0, make_capture())

    assert runtime.defers_archive_writes is False
    assert runtime._archive_worker is None
    assert saved.valid
    assert saved.path is None
    assert saved.archive_ready is None
    assert saved.archive["frames"].shape == (4, 2, 3)
    assert saved.metadata["capture_path"] is None
    assert saved.metadata["storage_format"] == "none"
    assert saved.metadata["npz_bytes"] is None
    assert saved.metadata["frame_count"] == 4
    assert list(tmp_path.iterdir()) == []


def test_stop_waits_for_background_archive_to_finish(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(frames_in_memory=True),
    )
    write_started = threading.Event()
    release_write = threading.Event()
    stop_returned = threading.Event()
    real_write = capture_runtime._write_archive_files

    def blocked_write(*args, **kwargs):
        write_started.set()
        assert release_write.wait(timeout=2.0)
        real_write(*args, **kwargs)

    monkeypatch.setattr(capture_runtime, "_write_archive_files", blocked_write)
    runtime._start_archive_writer()
    saved = runtime._save_capture(1, 123.0, make_capture())
    assert write_started.wait(timeout=2.0)

    stopper = threading.Thread(target=lambda: (runtime.stop(), stop_returned.set()))
    stopper.start()
    assert stop_returned.wait(timeout=0.1) is False

    release_write.set()
    stopper.join(timeout=2.0)

    assert stop_returned.is_set()
    assert saved.archive_ready.is_set()
    assert (saved.path / "frames.npz").is_file()
    assert runtime._archive_worker is None


def test_background_archive_failure_keeps_in_memory_capture_usable(tmp_path, monkeypatch, caplog):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(frames_in_memory=True),
    )

    def failing_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(capture_runtime, "_write_archive_files", failing_write)
    runtime._start_archive_writer()

    with caplog.at_level("WARNING", logger="openflight.camera.capture_runtime"):
        saved = runtime._save_capture(1, 123.0, make_capture())
        assert saved.archive_ready.wait(timeout=2.0)
        runtime.stop()

    assert saved.valid
    assert saved.archive["frames"].shape == (4, 2, 3)
    assert not (saved.path / "frames.npz").exists()
    assert "archive failed" in caplog.text


def test_live_image_controls_update_camera_without_restarting(tmp_path):
    class FakeCamera:
        def __init__(self):
            self.controls = []

        def set_controls(self, controls):
            self.controls.append(controls)

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=300.0, exposure_us=500, gain=2.0),
    )
    camera = FakeCamera()
    runtime._camera = camera
    runtime._running = True

    result = runtime.update_image_controls(exposure_us=750, gain=3.5)

    assert camera.controls == [{"ExposureTime": 750, "AnalogueGain": 3.5}]
    assert result == {"exposure_us": 750, "gain": 3.5}
    assert runtime.settings.exposure_us == 750
    assert runtime.settings.gain == 3.5


def test_auto_exposure_startup_jumps_to_brighter_setting(tmp_path):
    class FakeCamera:
        def __init__(self):
            self.controls = []

        def set_controls(self, controls):
            self.controls.append(controls)

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=488.0, exposure_us=250, gain=4.0),
    )
    runtime._camera = FakeCamera()
    runtime._running = True
    runtime._ring.add_frame(
        CameraFrame(
            image=np.full((200, 320), 18, dtype=np.uint8),
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=250,
            analogue_gain=4.0,
        )
    )

    decision = runtime._run_auto_exposure_cycle()

    assert decision is not None
    assert decision.status == "adjusting"
    assert runtime._camera.controls
    assert runtime.settings.exposure_us > 250
    assert runtime.auto_exposure_status()["analysis_eligible"] is False


def test_auto_exposure_defers_control_change_during_trigger_tail(tmp_path):
    class FakeCamera:
        def __init__(self):
            self.controls = []

        def set_controls(self, controls):
            self.controls.append(controls)

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            fps=488.0,
            pre_ms=1.0,
            post_ms=10.0,
            exposure_us=250,
            gain=4.0,
        ),
    )
    runtime._camera = FakeCamera()
    runtime._running = True
    runtime._ring.add_frame(
        CameraFrame(
            image=np.full((200, 320), 18, dtype=np.uint8),
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=250,
            analogue_gain=4.0,
        )
    )
    assert runtime._ring.trigger()

    decision = runtime._run_auto_exposure_cycle()

    assert decision is None
    assert runtime._camera.controls == []
    assert runtime.auto_exposure_status()["capture_deferred"] is True


def test_trigger_waits_for_in_progress_auto_exposure_update(tmp_path):
    controls_started = threading.Event()
    allow_controls = threading.Event()
    trigger_finished = threading.Event()

    class BlockingCamera:
        @staticmethod
        def set_controls(_controls):
            controls_started.set()
            assert allow_controls.wait(timeout=1.0)

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            fps=488.0,
            pre_ms=1.0,
            post_ms=10.0,
            exposure_us=250,
            gain=4.0,
        ),
    )
    runtime._camera = BlockingCamera()
    runtime._running = True
    runtime._ring.add_frame(
        CameraFrame(
            image=np.full((200, 320), 18, dtype=np.uint8),
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=250,
            analogue_gain=4.0,
        )
    )
    exposure_thread = threading.Thread(target=runtime._run_auto_exposure_cycle)
    trigger_thread = threading.Thread(
        target=lambda: (runtime.notify_trigger(), trigger_finished.set()),
    )

    exposure_thread.start()
    assert controls_started.wait(timeout=1.0)
    trigger_thread.start()
    assert not trigger_finished.wait(timeout=0.05)
    allow_controls.set()
    exposure_thread.join(timeout=1.0)
    trigger_thread.join(timeout=1.0)

    assert trigger_finished.is_set()
    trigger_state = runtime._trigger_auto_exposure.get_nowait()
    assert trigger_state["exposure_us"] == runtime.settings.exposure_us


def test_auto_exposure_acceptable_frame_enables_camera_analysis(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=488.0, exposure_us=500, gain=12.0),
    )
    runtime._camera = object()
    runtime._running = True
    image = make_good_exposure_image()
    runtime._ring.add_frame(
        CameraFrame(
            image=image,
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=500,
            analogue_gain=12.0,
        )
    )

    decision = runtime._run_auto_exposure_cycle()

    assert decision is not None
    assert decision.status == "ready"
    assert runtime.camera_analysis_eligible is True
    assert runtime.auto_exposure_status()["observation"]["status"] == "good"


def test_auto_exposure_locks_first_acceptable_startup_setting(tmp_path):
    class FakeCamera:
        def __init__(self):
            self.controls = []

        def set_controls(self, controls):
            self.controls.append(controls)

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=488.0, exposure_us=500, gain=12.0),
    )
    runtime._camera = FakeCamera()
    runtime._running = True
    runtime._ring.add_frame(
        CameraFrame(
            image=make_good_exposure_image(),
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=500,
            analogue_gain=12.0,
        )
    )
    startup_decision = runtime._run_auto_exposure_cycle()

    runtime._ring.add_frame(
        CameraFrame(
            image=np.full((200, 320), 18, dtype=np.uint8),
            sensor_timestamp_ns=3,
            host_timestamp_ns=4,
            exposure_us=500,
            analogue_gain=12.0,
        )
    )
    later_decision = runtime._run_auto_exposure_cycle()

    assert startup_decision is not None
    assert startup_decision.status == "ready"
    assert later_decision == startup_decision
    assert runtime._camera.controls == []
    assert (runtime.settings.exposure_us, runtime.settings.gain) == (500, 12.0)


def _runtime_locked_out_by_a_dark_start(tmp_path):
    """Runtime whose startup calibration ran in the dark at the top exposure step."""

    class FakeCamera:
        def set_controls(self, _controls):
            pass

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=300.0, exposure_us=1250, gain=16.0),
    )
    runtime._camera = FakeCamera()
    runtime._running = True
    _add_exposure_frame(runtime, np.full((200, 320), 18, dtype=np.uint8))
    assert runtime._run_auto_exposure_cycle().status == "lighting_required"
    return runtime


def _add_exposure_frame(runtime, image):
    runtime._ring.add_frame(
        CameraFrame(
            image=image,
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=runtime.settings.exposure_us,
            analogue_gain=runtime.settings.gain,
        )
    )


def test_lighting_verdict_from_a_dark_start_clears_once_the_lights_are_on(tmp_path):
    """A 9:58 pm start in a dark garage withheld camera analysis all next day."""
    runtime = _runtime_locked_out_by_a_dark_start(tmp_path)
    assert runtime.camera_analysis_eligible is False

    _add_exposure_frame(runtime, make_good_exposure_image())

    assert runtime._recalibrate_if_lighting_required() is True
    assert runtime.camera_analysis_eligible is True
    assert runtime.auto_exposure_status()["status"] == "ready"


def test_lighting_recheck_leaves_an_unchanged_dark_scene_alone(tmp_path):
    runtime = _runtime_locked_out_by_a_dark_start(tmp_path)
    checked_at = runtime.auto_exposure_status()["last_check_timestamp"]

    assert runtime._recalibrate_if_lighting_required() is False
    assert runtime.auto_exposure_status()["last_check_timestamp"] == checked_at
    assert runtime.camera_analysis_eligible is False


def test_lighting_recheck_never_unlocks_a_ready_setting(tmp_path):
    runtime = _runtime_locked_out_by_a_dark_start(tmp_path)
    _add_exposure_frame(runtime, make_good_exposure_image())
    runtime._recalibrate_if_lighting_required()

    _add_exposure_frame(runtime, np.full((200, 320), 18, dtype=np.uint8))

    assert runtime._recalibrate_if_lighting_required() is False
    assert runtime.camera_analysis_eligible is True


def test_auto_exposure_startup_calibration_is_synchronous(tmp_path, monkeypatch):
    calibration_started = threading.Event()
    allow_calibration_to_finish = threading.Event()
    startup_returned = threading.Event()
    runtime = CameraCaptureRuntime(output_dir=tmp_path)

    def calibrate():
        calibration_started.set()
        assert allow_calibration_to_finish.wait(timeout=1.0)

    monkeypatch.setattr(runtime, "_auto_exposure_loop", calibrate)

    def start_auto_exposure():
        runtime._start_auto_exposure()
        startup_returned.set()

    startup_thread = threading.Thread(target=start_auto_exposure)
    startup_thread.start()
    assert calibration_started.wait(timeout=1.0)
    returned_before_calibration = startup_returned.wait(timeout=0.05)
    allow_calibration_to_finish.set()
    startup_thread.join(timeout=1.0)

    assert returned_before_calibration is False
    assert startup_returned.is_set()


def test_auto_exposure_discards_calibration_frames_before_arming(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=100.0, pre_ms=20.0, post_ms=10.0),
    )
    runtime._ring.add_frame(make_frame(1))
    runtime._ring.add_frame(make_frame(2))
    calibration_ring = runtime._ring
    buffered_when_refill_started = []
    monkeypatch.setattr(
        runtime,
        "_wait_for_prebuffer",
        lambda: buffered_when_refill_started.append(runtime._ring.buffered_frames),
    )

    runtime._refill_locked_exposure_prebuffer()

    assert runtime._ring is not calibration_ring
    assert buffered_when_refill_started == [0]


def test_auto_exposure_restores_last_good_controls_for_same_camera_mode(tmp_path):
    state_path = tmp_path / "camera-exposure.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "width": 320,
                "height": 200,
                "fps": 488.0,
                "exposure_us": 650,
                "gain": 15.0,
            }
        ),
        encoding="utf-8",
    )

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=320,
            height=200,
            fps=488.0,
            exposure_us=250,
            gain=4.0,
            auto_exposure_state_path=state_path,
        ),
    )

    assert runtime.settings.exposure_us == 650
    assert runtime.settings.gain == 15.0


def test_auto_exposure_ignores_saved_controls_from_different_mode(tmp_path):
    state_path = tmp_path / "camera-exposure.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "width": 640,
                "height": 400,
                "fps": 300.0,
                "exposure_us": 1000,
                "gain": 20.0,
            }
        ),
        encoding="utf-8",
    )

    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=320,
            height=200,
            fps=488.0,
            exposure_us=250,
            gain=4.0,
            auto_exposure_state_path=state_path,
        ),
    )

    assert runtime.settings.exposure_us == 250
    assert runtime.settings.gain == 4.0


def test_auto_exposure_persists_good_controls(tmp_path):
    state_path = tmp_path / "camera-exposure.json"
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=320,
            height=200,
            fps=488.0,
            exposure_us=500,
            gain=12.0,
            auto_exposure_state_path=state_path,
        ),
    )
    runtime._camera = object()
    runtime._running = True
    image = make_good_exposure_image()
    runtime._ring.add_frame(
        CameraFrame(
            image=image,
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=500,
            analogue_gain=12.0,
        )
    )

    runtime._run_auto_exposure_cycle()

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["exposure_us"] == 500
    assert saved["gain"] == 12.0
    assert saved["width"] == 320


def test_preview_roll_correction_levels_sloped_line_without_modifying_raw_frame(tmp_path):
    cv2 = pytest.importorskip("cv2")
    correction_deg = 10.0
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(roll_correction_deg=correction_deg),
    )
    image = np.zeros((101, 101), dtype=np.uint8)
    tangent = np.tan(np.radians(correction_deg))
    cv2.line(image, (10, round(50 + 40 * tangent)), (90, round(50 - 40 * tangent)), 255, 2)
    frame = CameraFrame(
        image=image,
        sensor_timestamp_ns=1,
        host_timestamp_ns=2,
        exposure_us=500,
        analogue_gain=2.0,
    )
    runtime._ring.add_frame(frame)
    runtime._camera = object()
    runtime._running = True

    encoded = runtime.capture_preview_jpeg(quality=100)

    assert encoded is not None
    preview = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert preview.shape == image.shape
    rows, columns = np.where(preview > 180)
    slope = np.polyfit(columns, rows, 1)[0]
    assert abs(slope) < 0.03
    assert np.array_equal(runtime._ring.latest_frame.image, image)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (18, "too_dark"),
        (110, "good"),
        (252, "too_bright"),
    ],
)
def test_exposure_quality_rates_impact_zone(tmp_path, value, expected):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    image = np.full((200, 320), value, dtype=np.uint8)
    if expected == "good":
        image[100:175, 90:230] = np.tile(np.linspace(45, 185, 140, dtype=np.uint8), (75, 1))
    runtime._ring.add_frame(
        CameraFrame(
            image=image,
            sensor_timestamp_ns=1,
            host_timestamp_ns=2,
            exposure_us=500,
            analogue_gain=2.0,
        )
    )

    quality = runtime.exposure_quality()

    assert quality["status"] == expected
    assert quality["sample_available"] is True
    assert quality["recommendation"] in {"brighter", "darker", "hold"}


@pytest.mark.parametrize(
    ("exposure_us", "gain", "message"),
    [
        (0, 2.0, "exposure"),
        (1000, 0.0, "gain"),
        (4000, 2.0, "frame period"),
    ],
)
def test_live_image_controls_reject_invalid_values(tmp_path, exposure_us, gain, message):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(fps=300.0),
    )
    runtime._camera = object()
    runtime._running = True

    with pytest.raises(ValueError, match=message):
        runtime.update_image_controls(exposure_us=exposure_us, gain=gain)


def test_vertical_crop_limits_fix_320x200_to_safe_ten_pixel_steps():
    assert vertical_crop_limits(320, 200) == {
        "min_px": -70,
        "max_px": 70,
        "step_px": 10,
    }
    assert vertical_crop_limits(640, 400) is None


def test_vertical_crop_update_restarts_camera_and_writes_driver_parameter(tmp_path, monkeypatch):
    parameter = tmp_path / "strip_y_offset"
    parameter.write_text("0\n", encoding="ascii")
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=320, height=200),
        vertical_offset_path=parameter,
    )
    calls = []
    runtime._running = True
    monkeypatch.setattr(runtime, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(runtime, "start", lambda: calls.append("start"))

    result = runtime.update_vertical_crop(10)

    assert calls == ["stop", "start"]
    assert parameter.read_text(encoding="ascii") == "10\n"
    assert result["vertical_offset_px"] == 10


@pytest.mark.parametrize("offset", [-80, -5, 80])
def test_vertical_crop_update_rejects_unsafe_or_unaligned_offsets(tmp_path, offset):
    parameter = tmp_path / "strip_y_offset"
    parameter.write_text("0\n", encoding="ascii")
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=320, height=200),
        vertical_offset_path=parameter,
    )
    runtime._running = True

    with pytest.raises(ValueError, match="vertical crop"):
        runtime.update_vertical_crop(offset)


def _run_save_loop(runtime, completed_timestamps, monkeypatch):
    """Publish one saved capture per completion time through the save loop."""
    stamps = iter(completed_timestamps)

    def fake_save(sequence, trigger_epoch, _capture, **_kwargs):
        return capture_runtime.SavedCameraCapture(
            sequence=sequence,
            trigger_timestamp=trigger_epoch,
            completed_timestamp=next(stamps),
            path=None,
            metadata={},
        )

    monkeypatch.setattr(runtime, "_save_capture", fake_save)
    runtime._running = True
    for _ in completed_timestamps:
        runtime._ready.put(make_capture())
    runtime._ready.put(None)
    runtime._save_loop()


def test_unclaimed_captures_are_capped(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    now = time.time()

    _run_save_loop(runtime, [now] * 10, monkeypatch)

    assert len(runtime._captures) == capture_runtime._MAX_PENDING_CAPTURES
    assert [c.sequence for c in runtime._captures] == list(range(7, 11))


def test_unclaimed_captures_expire_by_age(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    old = time.time() - capture_runtime._MAX_PENDING_CAPTURE_AGE_S - 1.0

    _run_save_loop(runtime, [old, old, time.time()], monkeypatch)

    assert [c.sequence for c in runtime._captures] == [3]


def test_stop_closes_camera_even_when_camera_stop_fails(tmp_path):
    class FailingStopCamera:
        closed = False

        def stop(self):
            raise RuntimeError("libcamera stop failed")

        def close(self):
            self.closed = True

    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    camera = FailingStopCamera()
    runtime._camera = camera

    with pytest.raises(RuntimeError, match="libcamera stop failed"):
        runtime.stop()

    assert camera.closed
    assert runtime._camera is None
