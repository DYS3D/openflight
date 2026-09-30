"""Seeded random-input checks for the pass-2 features (spin axis, derived metrics,
flight payload, latency marks, radar health, strobe planning, camera spin,
processor flag combinations) and a stress run of the mock server pipeline.

Every loop is deterministic (fixed seeds) so a failure reproduces.
"""

import itertools
import json
import logging
import math
import random
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server as server_module
from openflight.ballistics import Trajectory, TrajectoryPoint
from openflight.camera import strobe
from openflight.camera.spin_from_pair import MarkPose, estimate_spin
from openflight.clubs import ClubType
from openflight.derived_metrics import derive, format_for_log
from openflight.gspro.codec import GSProCodec
from openflight.launch_monitor import Shot
from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.radar_health import RadarHealthMonitor
from openflight.rolling_buffer.types import BALL_MARKERS, IQCapture
from openflight.spin_axis import MAX_SPIN_AXIS_DEG, dplane_spin_axis

CLUBS = list(ClubType)


def _maybe(rng: random.Random, value, p_none: float = 0.3):
    return None if rng.random() < p_none else value


def _random_shot(rng: random.Random, mode: str = "rolling-buffer") -> Shot:
    club_speed = rng.choice([None, 0.0, rng.uniform(1.0, 140.0), 1e-9, 500.0])
    return Shot(
        ball_speed_mph=rng.choice([0.0, 15.0, rng.uniform(15.0, 220.0), 1e-6, 400.0]),
        club_speed_mph=club_speed,
        timestamp=datetime.now(),
        impact_timestamp=_maybe(rng, time.time()),
        club=rng.choice(CLUBS),
        launch_angle_vertical=_maybe(rng, rng.uniform(-30.0, 89.9)),
        launch_angle_horizontal=_maybe(rng, rng.uniform(-89.0, 89.0)),
        launch_angle_horizontal_confidence=_maybe(rng, rng.uniform(0.0, 1.0)),
        club_path_deg=_maybe(rng, rng.uniform(-45.0, 45.0)),
        club_angle_deg=_maybe(rng, rng.uniform(-20.0, 20.0)),
        spin_rpm=_maybe(rng, rng.choice([0.0, rng.uniform(100.0, 15000.0)])),
        spin_confidence=_maybe(rng, rng.uniform(0.0, 1.0)),
        spin_source=rng.choice([None, "measured", "calculated", "mock"]),
        spin_axis_deg=_maybe(rng, rng.uniform(-90.0, 90.0)),
        mode=mode,
    )


def _random_trajectory(rng: random.Random) -> Trajectory:
    count = rng.choice([0, 1, 2, 39, 40, 41, 200])
    points = [
        TrajectoryPoint(
            i * 0.05, i * 1.5, rng.uniform(-5, 5), abs(math.sin(i / 7.0)) * 30, 100.0, 2500.0
        )
        for i in range(count)
    ]
    return Trajectory(
        points=points,
        carry_yards=rng.choice([0.0, -3.0, rng.uniform(1.0, 350.0)]),
        apex_yards=rng.uniform(0.0, 60.0),
        lateral_yards=rng.uniform(-60.0, 60.0),
        flight_time_s=rng.uniform(0.0, 10.0),
        landing_speed_mph=rng.uniform(0.0, 120.0),
        landing_angle_deg=rng.uniform(-90.0, 90.0),
    )


class TestDerivedMetricsFuzz:
    def test_random_shots_and_trajectories_never_raise(self):
        rng = random.Random(1)
        for _ in range(400):
            shot = _random_shot(rng, mode=rng.choice(["rolling-buffer", "mock"]))
            trajectory = rng.choice([None, _random_trajectory(rng)])
            derived = derive(shot, trajectory)
            for key, entry in derived.items():
                assert set(entry) == {"value", "source"}, key
                assert entry["source"] in ("measured", "estimated")
                if key == "shot_shape":
                    assert isinstance(entry["value"], str)
                else:
                    assert isinstance(entry["value"], float)
                    assert math.isfinite(entry["value"]), (key, entry)
            format_for_log(derived)
            json.dumps(derived)
            if shot.club_speed_mph in (None, 0.0):
                assert "smash_factor" not in derived
            if trajectory is None:
                assert "apex_yards" not in derived


class TestDPlaneFuzz:
    def test_axis_is_bounded_and_sign_follows_face_to_path(self):
        rng = random.Random(2)
        for _ in range(2000):
            hla = rng.uniform(-60.0, 60.0)
            path = rng.uniform(-45.0, 45.0)
            estimate = dplane_spin_axis(
                launch_horizontal_deg=hla,
                club_path_deg=path,
                launch_vertical_deg=rng.uniform(-20.0, 89.0),
                club=rng.choice(CLUBS),
                attack_angle_deg=_maybe(rng, rng.uniform(-30.0, 30.0)),
            )
            assert math.isfinite(estimate.spin_axis_deg)
            assert abs(estimate.spin_axis_deg) <= MAX_SPIN_AXIS_DEG
            assert estimate.attack_angle_source in ("club_default", "measured")
            if hla - path > 1e-6:
                assert estimate.spin_axis_deg > 0
            elif hla - path < -1e-6:
                assert estimate.spin_axis_deg < 0
            else:
                assert estimate.spin_axis_deg == 0


class TestFlightPayloadFuzz:
    def test_flight_payload_is_bounded_and_json_safe(self):
        rng = random.Random(3)
        for _ in range(300):
            trajectory = _random_trajectory(rng)
            carry = rng.choice([None, 0.0, rng.uniform(1.0, 300.0)])
            payload = server_module._flight_payload(trajectory, carry_yards=carry)
            assert len(payload["points"]) <= server_module.FLIGHT_MAX_POINTS
            assert len(payload["points"]) == min(
                len(trajectory.points), server_module.FLIGHT_MAX_POINTS
            )
            if trajectory.points and len(trajectory.points) > 1:
                assert payload["points"][-1][0] == pytest.approx(
                    trajectory.points[-1].x
                    * (
                        carry / trajectory.carry_yards
                        if carry is not None and trajectory.carry_yards > 0
                        else 1.0
                    ),
                    abs=0.01,
                )
            json.dumps(payload)
            for value in payload.values():
                if isinstance(value, float):
                    assert math.isfinite(value)

    def test_mock_flight_lands_at_the_displayed_carry(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_density", server_module.AIR_DENSITY_STD)
        rng = random.Random(4)
        for _ in range(60):
            shot = _random_shot(rng, mode="mock")
            shot.launch_angle_vertical = rng.uniform(0.5, 45.0)
            shot.launch_angle_horizontal = rng.uniform(-20.0, 20.0)
            shot.spin_axis_deg = rng.uniform(-45.0, 45.0)
            shot.ball_speed_mph = rng.uniform(30.0, 200.0)
            trajectory = server_module._attach_mock_flight(shot)
            assert trajectory is not None
            assert shot.flight["carry_yards"] == pytest.approx(shot.estimated_carry_yards, abs=0.06)
            assert shot.flight["points"][-1][0] == pytest.approx(
                shot.estimated_carry_yards, abs=0.06
            )


class TestLatencyMarksFuzz:
    def test_latency_is_non_negative_rounded_and_keyed_by_mark(self):
        rng = random.Random(5)
        stages = ["capture", "processed", "initial_ui", "iwr6843", "camera", "carry", "final"]
        for _ in range(500):
            impact = _maybe(rng, rng.uniform(1.0e9, 2.0e9))
            shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=impact)
            marked = rng.sample(stages, rng.randint(0, len(stages)))
            for stage in marked:
                shot.mark_stage(stage, (impact or 1.0e9) + rng.uniform(-1.0, 5.0))
            latency = shot.latency_ms()
            assert set(latency) == set(marked)
            for stage, value in latency.items():
                if impact is None:
                    assert value is None
                else:
                    assert value >= 0.0
                    assert value == round(value, 1)
                    assert value == pytest.approx(
                        max(0.0, (shot.pipeline_marks[stage] - impact) * 1000.0), abs=0.05
                    )
            assert "pipeline_marks" not in shot.to_dict()


class TestRadarHealthFuzz:
    @staticmethod
    def _capture(level: float, rng: np.random.Generator) -> IQCapture:
        n = 4096
        noise = rng.normal(0.0, level, n)
        return IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=np.clip(2048 + noise, 0, 4095).astype(int).tolist(),
            q_samples=np.clip(2048 + noise[::-1], 0, 4095).astype(int).tolist(),
        )

    def test_transitions_obey_rise_consecutive_and_clear_rules(self):
        rng = np.random.default_rng(6)
        for run in range(12):
            events = []
            monitor = RadarHealthMonitor(
                RollingBufferProcessor(),
                on_change=events.append,
                rise_db=6.0,
                clear_db=3.0,
                consecutive=3,
                baseline_alpha=0.2,
            )
            interference = False
            elevated = 0
            baseline = None
            for _ in range(40):
                level = float(rng.choice([2.0, 3.0, 8.0, 20.0, 60.0]))
                capture = self._capture(level, rng)
                before = monitor.interference
                result = monitor.process(capture)
                floor = monitor.noise_floor_db
                assert result is monitor.interference
                assert floor is not None and math.isfinite(floor)
                if baseline is None:
                    baseline = floor
                rise = floor - baseline
                if rise > 6.0:
                    elevated += 1
                else:
                    elevated = 0
                    baseline += 0.2 * (floor - baseline)
                if not interference and elevated >= 3:
                    interference = True
                elif interference and rise <= 3.0:
                    interference = False
                assert monitor.interference is interference, (run, level, floor, baseline)
                assert monitor.baseline_db == pytest.approx(baseline)
                if before != monitor.interference:
                    assert events[-1]["interference"] is monitor.interference
                    assert set(events[-1]) == {
                        "interference",
                        "noise_floor_db",
                        "baseline_db",
                        "updated_at",
                    }
            assert len(events) == sum(
                1
                for a, b in zip(
                    [False] + [e["interference"] for e in events],
                    [e["interference"] for e in events],
                )
                if a != b
            )

    def test_observe_is_rate_limited_and_worker_survives_bad_captures(self, caplog):
        seen = []
        monitor = RadarHealthMonitor(
            RollingBufferProcessor(), on_change=seen.append, min_interval_s=0.0
        )
        monitor.start()
        try:
            bad = IQCapture(sample_time=0.0, trigger_time=0.0, i_samples=[], q_samples=[])
            with caplog.at_level(logging.WARNING, logger="openflight.rolling_buffer.radar_health"):
                monitor.observe(bad)
                monitor.observe(
                    IQCapture(sample_time=0.0, trigger_time=0.0, i_samples=[1, 2], q_samples=[3])
                )
                time.sleep(0.2)
                assert monitor._thread.is_alive()
                good = self._capture(5.0, np.random.default_rng(7))
                monitor.observe(good)
                time.sleep(0.3)
            assert monitor.noise_floor_db is not None
        finally:
            monitor.stop()


class TestStrobeFuzz:
    def test_gap_and_plan_stay_inside_the_hardware_window(self):
        rng = random.Random(8)
        for _ in range(500):
            rpm = rng.choice([0.0, -50.0, 1.0, rng.uniform(100.0, 20000.0), 1e7])
            target = rng.uniform(1.0, 120.0)
            gap = strobe.strobe_gap_for_spin(rpm, target)
            assert strobe.GAP_MIN_S <= gap <= strobe.GAP_MAX_S
            plan = strobe.plan_strobe(
                rng.choice(CLUBS), t0_s=rng.uniform(0.0, 0.05), target_deg=target, expected_rpm=rpm
            )
            assert plan.pulse_times_s[1] - plan.pulse_times_s[0] == pytest.approx(gap)
            assert plan.pulse_us * 1e-6 < plan.gap_s
            edges = strobe.NoOpPulseEmitter().fire(plan)
            assert strobe.measured_gap_s(edges) == pytest.approx(gap, abs=1e-9)
        with pytest.raises(ValueError):
            strobe.strobe_gap_for_spin(2500.0, 0.0)
        with pytest.raises(ValueError):
            strobe.exposure_for_speed(0.0, 1.0, 1000.0)
        assert strobe.measured_gap_s([1.0]) is None


class TestCameraSpinFuzz:
    @staticmethod
    def _pose(rng: np.random.Generator, with_normal: bool) -> MarkPose:
        center = rng.normal(size=3)
        center[2] = abs(center[2]) + 0.1
        center /= np.linalg.norm(center)
        normal = None
        if with_normal:
            candidate = rng.normal(size=3)
            candidate -= (candidate @ center) * center
            normal = candidate / np.linalg.norm(candidate)
        return MarkPose(
            angle_deg=float(rng.uniform(0, 180)),
            offset=(0.0, 0.0),
            center=center,
            normal=normal,
            pixel_count=int(rng.integers(8, 200)),
            elongation=float(rng.uniform(1, 10)),
            quality=float(rng.uniform(0, 1)),
        )

    def test_estimate_spin_never_raises_and_reports_consistent_components(self):
        rng = np.random.default_rng(9)
        for _ in range(500):
            poses = [
                self._pose(rng, bool(rng.integers(0, 2))) for _ in range(int(rng.integers(0, 4)))
            ]
            gap = float(rng.choice([-1.0, 0.0, 1e-4, 3.5e-3, 0.01]))
            result = estimate_spin(poses, gap)
            if gap <= 0:
                assert result.status == "invalid_gap"
                continue
            if len(poses) != 2:
                assert result.status in ("no_discs", "one_disc")
                assert result.spin_rpm is None
                continue
            assert result.status == "accepted"
            assert 0.0 <= result.angle_deg <= 180.0
            assert result.spin_rpm >= 0.0 and math.isfinite(result.spin_rpm)
            assert 0.0 <= result.confidence <= 1.0
            assert np.linalg.norm(result.axis) == pytest.approx(1.0)
            components = math.sqrt(
                result.backspin_rpm**2 + result.sidespin_rpm**2 + result.rifle_rpm**2
            )
            assert components == pytest.approx(result.spin_rpm, abs=1e-6)
            assert -180.0 <= result.axis_deg <= 180.0
        identical = self._pose(rng, True)
        same = estimate_spin([identical, identical], 3.5e-3)
        assert same.status == "accepted" and same.spin_rpm == pytest.approx(0.0)


def _synthetic_capture(rng: np.random.Generator, kind: str, sample_rate_hz: float) -> IQCapture:
    n = 4096
    t = np.arange(n) / sample_rate_hz
    p = RollingBufferProcessor

    def doppler(speed_mph: float) -> float:
        return (speed_mph / p.MPS_TO_MPH) / (p.WAVELENGTH_M / 2)

    if kind == "noise":
        i = rng.integers(0, 4096, n)
        q = rng.integers(0, 4096, n)
    elif kind == "constant":
        i = np.full(n, int(rng.integers(0, 4096)))
        q = np.full(n, int(rng.integers(0, 4096)))
    elif kind == "tone":
        hz = doppler(rng.uniform(16.0, 220.0))
        amp = rng.uniform(20.0, 2000.0) * (
            1 + rng.uniform(0, 0.4) * np.sin(2 * np.pi * rng.uniform(15, 250) * t)
        )
        noise = rng.normal(0, rng.uniform(0, 200), n)
        i = np.clip(2048 + amp * np.cos(2 * np.pi * hz * t) + noise, 0, 4095).astype(int)
        q = np.clip(2048 + amp * np.sin(2 * np.pi * hz * t) + noise, 0, 4095).astype(int)
    else:  # club then ball with a modulated envelope and clipping
        club = rng.uniform(40.0, 120.0)
        ball = club * rng.uniform(1.1, 1.5)
        onset = int(rng.integers(1000, 3000))
        idx = np.arange(n)
        club_amp = np.where(idx < onset, rng.uniform(300, 2500), 0.0)
        ball_amp = np.where(
            idx >= onset,
            rng.uniform(300, 2500) * np.exp(-(idx - onset) / rng.uniform(800, 4000)),
            0.0,
        )
        modulation = 1 + rng.uniform(0.0, 0.5) * np.sin(2 * np.pi * rng.uniform(15, 250) * t)
        signal = club_amp * np.exp(
            1j * 2 * np.pi * doppler(club) * t
        ) + ball_amp * modulation * np.exp(1j * 2 * np.pi * doppler(ball) * t)
        i = np.clip(2048 + signal.real + rng.normal(0, 30, n), 0, 4095).astype(int)
        q = np.clip(2048 + signal.imag + rng.normal(0, 30, n), 0, 4095).astype(int)
    return IQCapture(
        sample_time=0.0,
        trigger_time=float(rng.uniform(0.0, 0.13)),
        i_samples=i.tolist(),
        q_samples=q.tolist(),
        sample_rate_hz=sample_rate_hz,
    )


@pytest.fixture(scope="module")
def captures():
    rng = np.random.default_rng(10)
    kinds = ("noise", "constant", "tone", "clubball")
    return [
        (rate, _synthetic_capture(rng, kinds[k % 4], rate))
        for rate in (30000.0, 50000.0)
        for k in range(8)
    ]


def _processed_summary(processed) -> tuple:
    if processed is None:
        return ()
    spin = processed.spin
    spin_summary = None
    if spin is not None:
        spin_summary = (
            spin.spin_rpm,
            spin.confidence,
            spin.quality,
            spin.method,
            spin.snr,
            spin.rejection_reason,
        )
    return (
        processed.ball_speed_mph,
        processed.ball_timestamp_ms,
        processed.club_speed_mph,
        spin_summary,
        len(processed.timeline.readings),
    )


class TestProcessorFlagCombinationsFuzz:
    FLAG_COMBOS = list(itertools.product(BALL_MARKERS, (False, True), (False, True), (False, True)))

    @staticmethod
    def _process(processor: RollingBufferProcessor, capture: IQCapture, club: ClubType):
        return processor.process_capture(
            capture,
            expected_spin_for_ball_speed=lambda ball: get_optimal_spin_for_ball_speed(ball, club),
            club_type=club,
        )

    def test_every_flag_combination_processes_random_iq_without_raising(self, captures):
        rng = random.Random(11)
        for combo_index, (marker, octave, scale, fast) in enumerate(self.FLAG_COMBOS):
            # Each combination sees a rotating half of the capture set.
            for rate, capture in captures[combo_index % 2 :: 2]:
                processor = RollingBufferProcessor(
                    sample_rate=int(rate),
                    ball_marker=marker,
                    spin_octave_check=octave,
                    scale_speed_band=scale,
                    fast_dsp=fast,
                )
                processed = self._process(processor, capture, rng.choice(CLUBS))
                if processed is None:
                    continue
                spin = processed.spin
                if spin is not None:
                    assert spin.estimator == spin.method.split("+", 1)[0]
                    if marker == "none" and not octave:
                        assert "+" not in spin.method
                    if marker != "none":
                        assert f"marker_{marker}" in spin.method
                    if spin.octave_corrected:
                        assert octave
                for reading in processed.timeline.readings:
                    assert math.isfinite(reading.speed_mph)

    def test_flags_off_matches_the_default_constructor(self, captures):
        explicit = RollingBufferProcessor(
            sample_rate=30000,
            ball_marker="none",
            spin_octave_check=False,
            scale_speed_band=False,
            fast_dsp=False,
        )
        default = RollingBufferProcessor()
        assert (explicit.DC_MASK_BINS, explicit.MIN_PEAK_SEPARATION_BINS) == (
            RollingBufferProcessor.DC_MASK_BINS,
            RollingBufferProcessor.MIN_PEAK_SEPARATION_BINS,
        )
        for rate, capture in captures:
            if rate != 30000.0:
                continue
            assert _processed_summary(
                self._process(explicit, capture, ClubType.DRIVER)
            ) == _processed_summary(self._process(default, capture, ClubType.DRIVER))

    def test_fast_dsp_matches_the_default_path(self, captures):
        # fast_dsp only touches the speed FFT and peak picker, so the spin
        # options are held at their defaults here.
        for scale in (False, True):
            for rate, capture in captures:
                kwargs = dict(sample_rate=int(rate), scale_speed_band=scale)
                slow = RollingBufferProcessor(fast_dsp=False, **kwargs)
                fast = RollingBufferProcessor(fast_dsp=True, **kwargs)
                assert _processed_summary(
                    self._process(fast, capture, ClubType.IRON_7)
                ) == _processed_summary(self._process(slow, capture, ClubType.IRON_7))


# =============================================================================
# Server pipeline stress: mock monitor through the socket.io test client
# =============================================================================


def _wait_for_shot_finalization_idle(timeout_s: float = 5.0) -> None:
    with server_module._shot_finalization_condition:
        idle = server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=timeout_s,
        )
    assert idle, "shot finalization coordinator did not become idle"


@pytest.fixture
def mock_server(monkeypatch, tmp_path):
    server_module._reset_shot_sequence()
    monitor = server_module.MockLaunchMonitor()
    monitor.start(shot_callback=server_module.on_shot_detected)
    monkeypatch.setattr(server_module, "monitor", monitor)
    monkeypatch.setattr(server_module, "mock_mode", True)
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "level_monitor", None)
    monkeypatch.setattr(server_module, "update_service", None)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "debug_mode", False)
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "air_density", server_module.AIR_DENSITY_STD)
    monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
    monkeypatch.setattr(server_module, "show_normalized_carry", True)
    monkeypatch.setattr(server_module, "gated_postprocessing", False)
    monkeypatch.setattr(server_module, "request_rate_limit_per_s", 0.0)
    monkeypatch.setattr(server_module, "_rate_buckets", {})
    monkeypatch.setattr(server_module, "access_policy", server_module.AccessPolicy.disabled())
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module, "profile_store", server_module.ProfileStore(tmp_path / "profiles.json")
    )
    flask_client = server_module.app.test_client()
    client = server_module.socketio.test_client(server_module.app, flask_test_client=flask_client)
    assert client.is_connected()
    client.get_received()
    yield monitor, client
    _wait_for_shot_finalization_idle()
    client.disconnect()


class TestMockServerStress:
    def test_fifty_rapid_mock_shots_emit_consistent_payloads(self, mock_server, caplog):
        monitor, client = mock_server
        random.seed(12)
        ready_states = []
        with caplog.at_level(logging.WARNING):
            for _ in range(50):
                client.emit("simulate_shot")
                ready_states.append(server_module._launch_monitor_ready_state())
            _wait_for_shot_finalization_idle()
        time.sleep(0.05)

        problems = [
            record
            for record in caplog.records
            if record.levelno >= logging.ERROR or record.exc_info is not None
        ]
        assert problems == [], [record.getMessage() for record in problems]

        shots = [
            payload["args"][0] for payload in client.get_received() if payload["name"] == "shot"
        ]
        assert len(shots) == 50
        key_sets = {frozenset(entry["shot"]) for entry in shots}
        assert len(key_sets) == 1, "shot payload keys differ between shots"
        keys = next(iter(key_sets))
        for expected in ("flight", "derived", "latency_ms", "carry_spin_adjusted"):
            assert expected in keys, expected
        # Normalized carry is a re-simulation of a measured launch; mock shots
        # display the table carry and do not get one.
        assert "carry_normalized_yards" not in keys
        for leaked in ("pipeline_marks", "iwr6843_status", "camera_status"):
            assert leaked not in keys, leaked
        assert [entry["shot"]["shot_number"] for entry in shots] == list(range(1, 51))
        for entry in shots:
            shot = entry["shot"]
            assert "mode" not in shot and "readings" not in shot
            assert set(shot["latency_ms"]) == {"carry", "final"}
            assert all(value is None for value in shot["latency_ms"].values())
            assert shot["derived"]["smash_factor"]["source"] == "estimated"
            assert shot["flight"]["carry_yards"] == pytest.approx(
                shot["estimated_carry_yards"], abs=0.6
            )
            json.dumps(entry)
        stats = shots[-1]["stats"]
        assert stats["shot_count"] == 50
        assert stats["mode"] == "mock"
        assert monitor.get_session_stats()["shot_count"] == 50
        assert all(
            isinstance(ready, bool) and isinstance(ball, bool) for ready, ball in ready_states
        )
        assert server_module._launch_monitor_ready_state() == (True, True)

        codec = GSProCodec(
            device_id="OpenFlight", ready_state=server_module._launch_monitor_ready_state
        )
        beat = json.loads(codec.heartbeat_bytes())
        assert beat["ShotDataOptions"]["LaunchMonitorIsReady"] is True
        assert beat["ShotDataOptions"]["IsHeartBeat"] is True

        # Every shot object retained by the mock monitor still serialises to the payload it sent.
        for retained, entry in zip(monitor.get_shots(), shots):
            expected = dict(entry["shot"])
            expected.pop("latency_ms")
            assert json.loads(json.dumps(server_module.shot_to_dict(retained))) == expected


# =============================================================================
# Gated post-processing with a misbehaving IWR6843 runtime
# =============================================================================


class _MisbehavingIwrRuntime:
    """process_shot raises, hangs, or returns late depending on ``behaviours``."""

    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.calls = 0
        self.release = threading.Event()
        self.done = threading.Event()

    def process_shot(self, **_kwargs):
        behaviour = self.behaviours[self.calls]
        self.calls += 1
        if behaviour == "raise":
            raise RuntimeError("IWR dump exploded")
        if behaviour == "hang":
            self.release.wait(5.0)
        elif behaviour == "late":
            time.sleep(0.15)
        measurement = SimpleNamespace(
            accepted=True,
            angle_deg=14.0,
            status="accepted",
            n_snapshots=3,
            n_frames=20,
            component_std_deg=0.4,
            range_evidence=None,
            horizontal_deg=None,
            horizontal_confidence=None,
            horizontal_status=None,
            to_dict=lambda: {},
        )
        capture = SimpleNamespace(
            valid=True,
            sequence=self.calls,
            path=None,
            raw=b"",
            dump_duration_s=0.01,
            error=None,
            trigger_timestamp=None,
            temperature_report=None,
        )
        self.done.set()
        return SimpleNamespace(capture=capture, measurement=measurement, club_path=None)


class TestGatedPostprocessingMisbehaviour:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        server_module._reset_shot_sequence()
        self.emitted = []
        monkeypatch.setattr(server_module, "monitor", None)
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
        monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)
        monkeypatch.setattr(server_module, "derived_metrics_enabled", True)
        monkeypatch.setattr(server_module, "show_normalized_carry", True)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "sim_connectors", [])
        monkeypatch.setattr(server_module, "gated_postprocessing", True)
        monkeypatch.setattr(server_module, "_gated_stage_threads", {})
        monkeypatch.setattr(server_module, "_GATED_IWR6843_BUDGET_S", 0.05)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: self.emitted.append((event, json.loads(json.dumps(payload)))),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *args, **kwargs: threading.Thread(
                target=target, args=args, kwargs=kwargs, daemon=True
            ).start(),
        )
        yield
        _wait_for_shot_finalization_idle()

    @staticmethod
    def _shot() -> Shot:
        return Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime(2026, 9, 30, 12, 0, 0),
            impact_timestamp=time.time(),
            club=ClubType.DRIVER,
            mode="rolling-buffer",
            inclinometer={"applied": True, "roll_deg": 1.0},
        )

    def test_raising_hanging_and_late_stages_never_lose_or_mutate_a_shot(self, monkeypatch, caplog):
        runtime = _MisbehavingIwrRuntime(["raise", "hang", "late", "ok"])
        monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
        shots = [self._shot() for _ in range(4)]

        with caplog.at_level(logging.INFO, logger="openflight.server"):
            server_module.on_shot_detected(shots[0])
            _wait_for_shot_finalization_idle()
            server_module.on_shot_detected(shots[1])
            _wait_for_shot_finalization_idle()
            server_module.on_shot_detected(shots[2])
            _wait_for_shot_finalization_idle()
            server_module.on_shot_detected(shots[3])
            _wait_for_shot_finalization_idle()
        finals = [payload for event, payload in self.emitted if event == "shot_update"]
        assert [payload["shot"]["shot_number"] for payload in finals] == [1, 2, 3, 4]
        snapshots = [json.loads(json.dumps(server_module.shot_to_dict(shot))) for shot in shots]

        raised, hung, late, fourth = (payload["shot"] for payload in finals)
        assert "iwr6843_status" not in raised
        assert raised["launch_angle_vertical_source"] == "estimated"
        assert hung["iwr6843_status"] == "skipped_budget"
        assert late["iwr6843_status"] == "skipped_budget"
        assert fourth["iwr6843_status"] == "skipped_budget"
        assert runtime.calls == 2, "the hung stage must keep later shots away from the hardware"
        for payload in finals:
            shot = payload["shot"]
            assert "derived" in shot and "flight" in shot and "carry_normalized_yards" in shot
            assert shot["latency_ms"]["final"] is not None
        assert any("Gated iwr6843 stage failed" in r.getMessage() for r in caplog.records) is False
        assert any(
            "IWR6843 processing failed" in r.getMessage() or "exploded" in r.getMessage()
            for r in caplog.records
        )

        runtime.release.set()
        assert runtime.done.wait(2.0)
        for thread in list(server_module._gated_stage_threads.values()):
            thread.join(2.0)
        for shot, before in zip(shots, snapshots):
            assert json.loads(json.dumps(server_module.shot_to_dict(shot))) == before
            assert shot.inclinometer == {"applied": True, "roll_deg": 1.0}
