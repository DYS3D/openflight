"""Selective L3 readback must give the estimator the full dump's answer."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import Calibration, estimate_lcmf_v1
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, pack_dump, parse_dump
from openflight.iwr6843.readback import (
    Readback,
    decode_summary,
    decode_windows_reply,
    encode_summary_reply,
    encode_windows_reply,
    extract_windows,
    plan_readback,
    prepare_readback,
    read_selective,
    summarize_capture,
    summary_reply_nbytes,
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


def _timed_capture(name: str) -> bytes:
    """The capture in the firmware's timed per-frame-window format."""
    metadata, cube = parse_dump(_capture(name))
    frames = metadata["n_frames"]
    return pack_dump(
        cube,
        n_tx=metadata["n_tx"],
        version=6,
        frame_period_us=metadata["frame_period_us"],
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(20,) * frames,
        range_bin_counts=(80,) * frames,
        frame_time_offsets_us=tuple(metadata["frame_period_us"] * i for i in range(frames)),
    )


class FrozenRadar:
    """Answers the readback commands from a capture, as the firmware would."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.summary = summarize_capture(raw)
        self.scopes: list[int] = []
        self.sent = 0

    def read_summary(self, scope: int) -> bytes:
        self.scopes.append(scope)
        reply = encode_summary_reply(self.summary, scope)
        self.sent += len(reply)
        return reply

    def read_windows(self, request: str, nbytes: int) -> bytes:
        pairs = [int(request[i : i + 2], 16) for i in range(0, len(request), 2)]
        windows = tuple(zip(pairs[0::2], pairs[1::2]))
        reply = encode_windows_reply(windows, extract_windows(self.raw, windows))
        assert len(reply) == nbytes
        self.sent += len(reply)
        return reply


@pytest.mark.parametrize("name", sorted(SHOTS))
def test_selective_read_over_the_wire_reproduces_the_full_dump_estimate(cal, name):
    raw = _timed_capture(name)
    speed_mph = SHOTS[name]["speed_ms"] * 2.23694
    full = estimate_lcmf_v1(raw, cal, ball_speed_mph=speed_mph, club="9i")
    radar = FrozenRadar(raw)

    readback = read_selective(radar, club="9i")

    assert readback is not None and readback.covers(club="9i", net_range_m=None)
    sparse = estimate_lcmf_v1(
        b"", cal, ball_speed_mph=speed_mph, club="9i", prepared=readback.prepare()
    )
    assert sparse.status == full.status
    assert sparse.angle_deg == pytest.approx(full.angle_deg, abs=1e-6)
    assert sparse.horizontal_deg == pytest.approx(full.horizontal_deg, abs=1e-6)
    assert radar.sent < 0.35 * len(raw)


def test_window_scope_summary_is_fetched_only_for_a_notched_ball():
    clean = FrozenRadar(_timed_capture("iron"))
    notched = FrozenRadar(_timed_capture("mti_notch_speed"))

    read_selective(clean, club="9i")
    read_selective(notched, club="9i")

    assert clean.scopes == [0]
    assert notched.scopes == [0, 1]


def test_summary_reply_round_trips():
    summary = summarize_capture(_timed_capture("iron"))

    decoded = decode_summary(encode_summary_reply(summary, 0), encode_summary_reply(summary, 1))

    for scope in ("burst", "window"):
        np.testing.assert_allclose(decoded.loop_power[scope], summary.loop_power[scope], rtol=1e-6)
        assert decoded.noise_power[scope] == summary.noise_power[scope]
    np.testing.assert_array_equal(decoded.window_mean, summary.window_mean)
    for key in ("range_bin_starts", "range_bin_counts", "frame_time_offsets_us", "n_samples"):
        assert decoded.metadata[key] == summary.metadata[key]


def test_summary_reply_length_is_known_from_its_first_bytes():
    summary = summarize_capture(_timed_capture("iron"))
    for scope in (0, 1):
        reply = encode_summary_reply(summary, scope)
        assert summary_reply_nbytes(reply[:8]) is None
        assert summary_reply_nbytes(reply[:200]) == len(reply)


def test_truncated_or_mismatched_replies_are_refused():
    radar = FrozenRadar(_timed_capture("iron"))
    readback = read_selective(radar, club="9i")
    reply = encode_windows_reply(readback.windows, readback.samples)
    other = tuple((low + 1, count) if count else (0, 0) for low, count in readback.windows)

    with pytest.raises(ValueError, match="truncated"):
        decode_summary(encode_summary_reply(radar.summary, 0)[:-1])
    with pytest.raises(ValueError, match="truncated"):
        decode_windows_reply(reply[:-4], readback.summary, readback.windows)
    with pytest.raises(ValueError, match="different window request"):
        decode_windows_reply(reply, readback.summary, other)


def test_readback_does_not_cover_a_track_outside_its_windows():
    readback = read_selective(FrozenRadar(_timed_capture("iron")), club="9i")
    narrowed = tuple((low + 2, count - 2) if count else (0, 0) for low, count in readback.windows)

    clipped = Readback(readback.summary, narrowed, readback.samples)

    assert not clipped.covers(club="9i", net_range_m=None)
