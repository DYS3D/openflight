"""Synthetic marked-ball spin estimation and strobe planning."""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("cv2")

from openflight.camera import spin_from_pair as sfp, strobe  # noqa: E402
from openflight.clubs import ClubType  # noqa: E402

RPM_TO_DEG_PER_S = 6.0


def rodrigues(axis, angle_deg):
    a = np.asarray(axis, dtype=float)
    a /= np.linalg.norm(a)
    theta = math.radians(angle_deg)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(theta) * k + (1 - math.cos(theta)) * (k @ k)


def render_ball(
    canvas,
    cx,
    cy,
    r,
    rotation=None,
    *,
    mark_normal=(0.0, 1.0, 0.0),
    mark_center=(0.0, 0.0, 1.0),
    arc_half_deg=35.0,
    half_width_deg=6.0,
    ball_level=220,
    mark_level=40,
):
    """Paint a ball with a great-circle arc mark, rotated by ``rotation``."""
    rotation = np.eye(3) if rotation is None else rotation
    h, w = canvas.shape
    ys, xs = np.mgrid[0:h, 0:w]
    u = (xs + 0.5 - cx) / r
    v = (ys + 0.5 - cy) / r
    inside = u**2 + v**2 <= 1.0
    canvas[inside] = ball_level
    if mark_level is None:
        return canvas
    ww = np.sqrt(np.clip(1.0 - u**2 - v**2, 0.0, None))
    points = np.stack([u, v, ww], axis=-1) @ rotation  # rows: p @ R == R^T p
    n = np.asarray(mark_normal, dtype=float)
    n /= np.linalg.norm(n)
    m = np.asarray(mark_center, dtype=float)
    m /= np.linalg.norm(m)
    on_plane = np.abs(points @ n) < math.sin(math.radians(half_width_deg))
    on_arc = points @ m > math.cos(math.radians(arc_half_deg))
    canvas[inside & on_plane & on_arc] = mark_level
    return canvas


def blank(h=200, w=320):
    return np.full((h, w), 12, dtype=np.uint8)


def pair_frames(rotation, r=40.0):
    frame_a = render_ball(blank(), 120.0, 100.0, r)
    frame_b = render_ball(blank(), 135.0, 90.0, r, rotation)
    return frame_a, frame_b


class TestFindDiscs:
    def test_two_separate_discs_left_to_right(self):
        frame = render_ball(blank(), 80.0, 100.0, 30.0)
        frame = render_ball(frame, 200.0, 110.0, 32.0)
        discs = sfp.find_discs(frame)
        assert len(discs) == 2
        assert discs[0].cx < discs[1].cx
        assert discs[0].cx == pytest.approx(80.0, abs=1.0)
        assert discs[1].r == pytest.approx(32.0, abs=1.5)

    def test_touching_discs_are_split(self):
        frame = render_ball(blank(), 120.0, 100.0, 30.0)
        frame = render_ball(frame, 172.0, 100.0, 30.0)
        discs = sfp.find_discs(frame)
        assert len(discs) == 2
        assert discs[0].cx == pytest.approx(120.0, abs=3.0)
        assert discs[1].cx == pytest.approx(172.0, abs=3.0)

    def test_empty_frame(self):
        assert sfp.find_discs(blank()) == []


class TestMarkPose:
    def test_line_mark_recovers_plane_normal(self):
        frame = render_ball(blank(), 120.0, 100.0, 40.0)
        disc = sfp.find_discs(frame)[0]
        crop, local = sfp.extract_disc(frame, disc)
        pose = sfp.mark_pose(crop, local)
        assert pose is not None
        assert pose.normal is not None
        assert abs(float(pose.normal @ np.array([0.0, 1.0, 0.0]))) > 0.98
        assert pose.offset == pytest.approx((0.0, 0.0), abs=0.05)
        assert pose.angle_deg == pytest.approx(0.0, abs=3.0) or pose.angle_deg > 177.0

    def test_dot_mark_has_no_normal(self):
        frame = render_ball(blank(), 120.0, 100.0, 40.0, arc_half_deg=7.0, half_width_deg=7.0)
        disc = sfp.find_discs(frame)[0]
        pose = sfp.mark_pose(*sfp.extract_disc(frame, disc))
        assert pose is not None
        assert pose.normal is None

    def test_plain_ball_has_no_mark(self):
        frame = render_ball(blank(), 120.0, 100.0, 40.0, mark_level=None)
        disc = sfp.find_discs(frame)[0]
        assert sfp.mark_pose(*sfp.extract_disc(frame, disc)) is None


class TestEstimateSpin:
    @pytest.mark.parametrize(
        ("axis", "angle", "expected_axis_deg"),
        [
            ((-1.0, 0.0, 0.0), 30.0, 0.0),
            ((0.0, 1.0, 0.0), 25.0, 90.0),
            ((-math.cos(math.radians(20)), math.sin(math.radians(20)), 0.0), 35.0, 20.0),
            ((-1.0, 0.0, 0.0), 45.0, 0.0),
        ],
    )
    def test_consecutive_frames_recover_rotation(self, axis, angle, expected_axis_deg):
        rpm = 2700.0
        gap_s = angle / (rpm * RPM_TO_DEG_PER_S)
        frame_a, frame_b = pair_frames(rodrigues(axis, angle))
        result = sfp.estimate_spin_from_frames(frame_a, frame_b, gap_s)
        assert result.status == "accepted"
        assert result.angle_deg == pytest.approx(angle, abs=3.0)
        assert result.spin_rpm == pytest.approx(rpm, rel=0.05)
        assert result.axis_deg == pytest.approx(expected_axis_deg, abs=6.0)
        assert result.confidence > 0.5

    def test_backspin_component_sign(self):
        rotation = rodrigues((-1.0, 0.0, 0.0), 30.0)
        frame_a, frame_b = pair_frames(rotation)
        result = sfp.estimate_spin_from_frames(frame_a, frame_b, 0.00185)
        assert result.backspin_rpm > 0.9 * result.spin_rpm
        assert abs(result.sidespin_rpm) < 0.2 * result.spin_rpm

    def test_double_exposure_frame(self):
        rotation = rodrigues((-1.0, 0.0, 0.0), 30.0)
        frame = render_ball(blank(), 100.0, 100.0, 36.0)
        frame = render_ball(frame, 190.0, 96.0, 36.0, rotation)
        result = sfp.estimate_spin_from_double_exposure(frame, 0.002)
        assert result.status == "accepted"
        assert len(result.discs) == 2
        assert result.angle_deg == pytest.approx(30.0, abs=3.0)
        assert result.spin_rpm == pytest.approx(2500.0, rel=0.05)

    def test_dot_mark_falls_back_to_minimal_rotation(self):
        rotation = rodrigues((-1.0, 0.0, 0.0), 30.0)
        frame_a = render_ball(blank(), 120.0, 100.0, 40.0, arc_half_deg=7.0, half_width_deg=7.0)
        frame_b = render_ball(
            blank(), 130.0, 90.0, 40.0, rotation, arc_half_deg=7.0, half_width_deg=7.0
        )
        result = sfp.estimate_spin_from_frames(frame_a, frame_b, 0.00185)
        assert result.status == "accepted"
        assert result.angle_deg == pytest.approx(30.0, abs=3.0)
        assert result.confidence < 0.7

    def test_no_mark(self):
        frame_a = render_ball(blank(), 120.0, 100.0, 40.0, mark_level=None)
        frame_b = render_ball(blank(), 130.0, 90.0, 40.0, mark_level=None)
        assert sfp.estimate_spin_from_frames(frame_a, frame_b, 0.002).status == "no_mark"

    def test_one_disc_in_double_exposure(self):
        frame = render_ball(blank(), 120.0, 100.0, 40.0)
        result = sfp.estimate_spin_from_double_exposure(frame, 0.002)
        assert result.status == "one_disc"
        assert len(result.discs) == 1

    def test_no_discs_and_invalid_gap(self):
        assert sfp.estimate_spin_from_double_exposure(blank(), 0.002).status == "no_discs"
        frame_a, frame_b = pair_frames(rodrigues((-1.0, 0.0, 0.0), 30.0))
        assert sfp.estimate_spin_from_frames(frame_a, frame_b, 0.0).status == "invalid_gap"
        assert sfp.estimate_spin([], 0.002).status == "no_discs"


class TestStrobePlanning:
    def test_gap_targets_rotation_and_clamps(self):
        assert strobe.strobe_gap_for_spin(2700.0, 30.0) == pytest.approx(30.0 / (2700 * 6))
        assert strobe.strobe_gap_for_spin(100.0) == strobe.GAP_MAX_S
        assert strobe.strobe_gap_for_spin(50_000.0) == strobe.GAP_MIN_S
        assert strobe.strobe_gap_for_spin(0.0) == strobe.GAP_MAX_S
        with pytest.raises(ValueError):
            strobe.strobe_gap_for_spin(2700.0, 0.0)

    def test_exposure_for_speed(self):
        # 150 mph = 67.056 m/s; 1 px at 1000 px/m -> 14.9 us
        assert strobe.exposure_for_speed(150.0, 1.0, 1000.0) == pytest.approx(14.91e-6, rel=1e-3)
        with pytest.raises(ValueError):
            strobe.exposure_for_speed(0.0, 1.0, 1000.0)

    def test_plan_from_club_prior(self):
        plan = strobe.plan_strobe(ClubType.DRIVER, t0_s=0.004)
        assert plan.n_pulses == 2
        assert plan.gap_s == pytest.approx(
            strobe.strobe_gap_for_spin(strobe.expected_spin_rpm(ClubType.DRIVER))
        )
        assert plan.pulse_times_s == pytest.approx([0.004, 0.004 + plan.gap_s])
        wedge = strobe.plan_strobe(ClubType.LW, t0_s=0.004)
        assert wedge.gap_s < plan.gap_s

    def test_plan_validation(self):
        with pytest.raises(ValueError):
            strobe.StrobePlan(t0_s=0.0, gap_s=0.01)
        with pytest.raises(ValueError):
            strobe.StrobePlan(t0_s=0.0, gap_s=0.001, pulse_us=2000.0)
        with pytest.raises(ValueError):
            strobe.StrobePlan(t0_s=-1.0, gap_s=0.001)

    def test_noop_emitter_records_plans(self):
        emitter = strobe.NoOpPulseEmitter()
        plan = strobe.StrobePlan(t0_s=0.0, gap_s=0.002)
        edges = emitter.fire(plan)
        assert emitter.fired == [plan]
        assert strobe.measured_gap_s(edges) == pytest.approx(0.002)
        assert strobe.measured_gap_s(edges[:1]) is None

    def test_gpio_emitter_pulses_fake_output(self):
        events = []

        class FakeOutput:
            def on(self):
                events.append(("on", strobe.time.perf_counter()))

            def off(self):
                events.append(("off", strobe.time.perf_counter()))

        emitter = strobe.GpioPulseEmitter(18, output_factory=lambda _pin: FakeOutput())
        plan = strobe.StrobePlan(t0_s=0.001, gap_s=0.002, pulse_us=100.0)
        edges = emitter.fire(plan)
        assert [e[0] for e in events] == ["on", "off", "on", "off"]
        assert len(edges) == 2
        # A late thread start collapses both pulses together; the busy-wait
        # never stretches the spacing, so only the upper bound is stable.
        assert 0.0 <= strobe.measured_gap_s(edges) <= 0.004
