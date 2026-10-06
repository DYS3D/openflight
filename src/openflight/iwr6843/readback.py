"""Selective L3 readback: fetch the ball's range track instead of the whole ring.

The full capture takes about seven seconds to cross the UART, yet the launch
estimator reads only the samples along the ball's range walk. The radar can
instead send a small summary (the tables the ball search reads), let the host
choose a narrow range window per frame, and send only those samples.

This module is the reference model for that exchange. ``summarize_capture`` and
``extract_windows`` define exactly what the firmware must produce from its
frozen ring; the rest is the host side. A capture rebuilt by
``prepare_readback`` gives ``estimate_lcmf_v1`` the same answer as the full
dump, because every sample and table the estimator touches is present.

Not covered: the OPS-guided recovery search and the IWR club track read
samples away from the ball's track, so those still need the full dump.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TIMED_FRAME_DESCRIPTOR,
    is_range_snapshot,
    pack_dump,
    parse_dump,
)
from openflight.iwr6843.lcmf import PreparedLCMFCapture, prepare_lcmf_capture
from openflight.iwr6843.shot import burst_track_settled, find_scope_track, select_ball_track
from openflight.iwr6843.tracking import BallTrack

MTI_SCOPES = ("burst", "window")
# The estimator samples loops this far outside the fitted track span.
TRACK_TIME_PAD_S = 2e-3
# Bins fetched beyond the track on each side; the estimator needs one.
DEFAULT_MARGIN_BINS = 2

# Firmware replies to `l3sum <scope>` and `l3bins <hex>` (firmware/iwr6843/l3_dump.c).
SUMMARY_MAGIC = b"ILS1"
WINDOWS_MAGIC = b"ILB1"
_SUMMARY_HEADER = struct.Struct("<4sBBBBHHHH")
_SUMMARY_TRAILER = struct.Struct("<dII")
_WINDOWS_HEADER = struct.Struct("<4sH")
# A summary carries no temperature report, so it maps to the timed schema without one.
_READBACK_DUMP_VERSION = 6

Window = tuple[int, int]


@dataclass(frozen=True)
class CaptureSummary:
    """What the radar computes from its frozen ring so the host can find the ball.

    ``loop_power`` and ``noise_power`` are keyed by MTI scope and describe the
    vertical TX pair. ``window_mean`` is that pair's per-bin mean over the whole
    capture, shaped [tx, rx, absolute range bin]; window-scope MTI is a fetched
    sample minus this.
    """

    metadata: dict
    loop_power: dict[str, np.ndarray]
    noise_power: dict[str, float]
    window_mean: np.ndarray

    @property
    def nbytes(self) -> int:
        """Payload size if tables travel as float32 and means as complex64."""
        tables = sum(table.size for table in self.loop_power.values())
        return 4 * tables + 8 * self.window_mean.size + 4 * len(self.noise_power)


def _frame_windows(metadata: dict) -> list[Window]:
    """Absolute (first bin, bin count) stored for each frame of a capture."""
    starts = metadata.get("range_bin_starts")
    counts = metadata.get("range_bin_counts")
    n_frames = metadata["n_frames"]
    if starts is None:
        starts = (metadata.get("range_bin_start", 0),) * n_frames
    if counts is None:
        counts = (metadata["n_samples"],) * n_frames
    return list(zip(starts, counts))


def summarize_capture(raw: bytes) -> CaptureSummary:
    """Reference for the firmware's summary of one frozen capture."""
    prepared = prepare_lcmf_capture(raw)
    metadata = prepared.full_metadata
    if not is_range_snapshot(metadata):
        raise ValueError("selective readback needs a range-snapshot capture")
    vertical = prepared.vertical
    n_frames, chirps_per_frame, n_rx, n_samples = vertical.cube.shape
    range_movie = vertical.cube.reshape(n_frames, chirps_per_frame // 2, 2, n_rx, n_samples)
    range_movie = range_movie.transpose(0, 2, 1, 3, 4)
    removed = range_movie - vertical.mti("window")
    frame_windows = _frame_windows(metadata)
    last_bin = max(start + count for start, count in frame_windows)
    window_mean = np.zeros((2, n_rx, last_bin), dtype=complex)
    for frame, (start, count) in enumerate(frame_windows):
        window_mean[:, :, start : start + count] = removed[frame, :, 0, :, :count]
    return CaptureSummary(
        metadata=metadata,
        loop_power={scope: vertical.loop_power(scope) for scope in MTI_SCOPES},
        noise_power={scope: vertical.noise_power(scope) for scope in MTI_SCOPES},
        window_mean=window_mean,
    )


def track_windows(
    summary: CaptureSummary,
    track: BallTrack,
    *,
    margin_bins: int = DEFAULT_MARGIN_BINS,
) -> tuple[Window, ...]:
    """Per-frame (local first bin, bin count) covering the track; (0, 0) skips a frame."""
    geometry = prepare_readback(summary, (), ()).vertical.geometry
    last_loop = geometry.n_loops - 1
    windows: list[Window] = []
    for frame, (start, count) in enumerate(_frame_windows(summary.metadata)):
        first_s = geometry.loop_time(frame, 0)
        last_s = geometry.loop_time(frame, last_loop)
        if last_s < track.t_first - TRACK_TIME_PAD_S or first_s > track.t_last + TRACK_TIME_PAD_S:
            windows.append((0, 0))
            continue
        bins = [int(round(track.bin_at(time_s))) - start for time_s in (first_s, last_s)]
        low = max(0, min(bins) - margin_bins)
        high = min(count, max(bins) + margin_bins + 1)
        windows.append((low, high - low) if high > low else (0, 0))
    return tuple(windows)


def plan_readback(
    summary: CaptureSummary,
    *,
    club: str | None = None,
    net_range_m: float | None = None,
    margin_bins: int = DEFAULT_MARGIN_BINS,
) -> tuple[Window, ...] | None:
    """Windows to fetch for the ball the summary shows, or None when it shows none."""
    track, _notch_used = select_ball_track(
        prepare_readback(summary, (), ()).vertical, club=club, net_range_m=net_range_m
    )
    if track is None:
        return None
    return track_windows(summary, track, margin_bins=margin_bins)


def extract_windows(raw: bytes, windows: tuple[Window, ...]) -> tuple[np.ndarray, ...]:
    """Reference for the firmware's reply: each frame's [chirp, rx, bin] samples."""
    _metadata, cube = parse_dump(raw)
    return tuple(cube[frame, :, :, low : low + count] for frame, (low, count) in enumerate(windows))


def windows_nbytes(summary: CaptureSummary, windows: tuple[Window, ...]) -> int:
    """IQ16 payload size of the fetched windows."""
    per_bin = summary.metadata["chirps_per_frame"] * summary.metadata["n_rx"] * 4
    return per_bin * sum(count for _low, count in windows)


def prepare_readback(
    summary: CaptureSummary,
    windows: tuple[Window, ...],
    samples: tuple[np.ndarray, ...],
) -> PreparedLCMFCapture:
    """Rebuild an estimator-ready capture from a summary and its fetched windows.

    Samples that were not fetched are zero. Nothing reads them: the ball search
    uses the summary's tables, and the noise level and window-scope MTI are
    seeded from the summary instead of being derived from the sparse cube.
    """
    metadata = summary.metadata
    cube = np.zeros(
        (
            metadata["n_frames"],
            metadata["chirps_per_frame"],
            metadata["n_rx"],
            metadata["n_samples"],
        ),
        dtype=complex,
    )
    for frame, ((low, count), frame_samples) in enumerate(zip(windows, samples)):
        cube[frame, :, :, low : low + count] = frame_samples
    raw = pack_dump(
        cube,
        n_tx=metadata["n_tx"],
        trigger_frame=metadata["trigger_frame"],
        version=metadata["version"],
        frame_period_us=metadata.get("frame_period_us", 0),
        sample_fmt=metadata["sample_fmt"],
        range_bin_start=metadata.get("range_bin_start", 0),
        range_bin_starts=metadata.get("range_bin_starts"),
        range_bin_counts=metadata.get("range_bin_counts"),
        frame_time_offsets_us=metadata.get("frame_time_offsets_us"),
        temperature_report=metadata.get("temperature_report"),
    )
    prepared = prepare_lcmf_capture(raw)
    vertical = prepared.vertical
    n_frames, chirps_per_frame, n_rx, n_samples = vertical.cube.shape
    range_movie = vertical.cube.reshape(n_frames, chirps_per_frame // 2, 2, n_rx, n_samples)
    range_movie = range_movie.transpose(0, 2, 1, 3, 4)
    # pylint: disable=protected-access  # seeding the capture's own lazy caches
    if "window" in summary.loop_power:
        window_mti = np.zeros_like(range_movie)
        frame_windows = _frame_windows(metadata)
        for frame, (low, count) in enumerate(windows):
            first = frame_windows[frame][0] + low
            window_mti[frame, :, :, :, low : low + count] = (
                range_movie[frame, :, :, :, low : low + count]
                - summary.window_mean[:, None, :, first : first + count]
            )
        vertical._mti_by_scope["window"] = window_mti
    vertical._power_by_scope.update(summary.loop_power)
    vertical._noise_by_scope.update(summary.noise_power)
    return prepared


def encode_summary_reply(summary: CaptureSummary, scope: int) -> bytes:
    """Reference for the firmware's `l3sum <scope>` reply (0 burst, 1 window)."""
    metadata = summary.metadata
    name = MTI_SCOPES[scope]
    offsets = metadata["frame_time_offsets_us"]
    deltas = [0] + [int(b - a) for a, b in zip(offsets, offsets[1:])]
    parts = [
        _SUMMARY_HEADER.pack(
            SUMMARY_MAGIC,
            scope,
            metadata["n_tx"],
            metadata["n_rx"],
            0,
            metadata["chirps_per_frame"] // metadata["n_tx"],
            metadata["n_frames"],
            metadata["n_samples"],
            metadata.get("frame_period_us", 0),
        )
    ]
    for (start, count), delta in zip(_frame_windows(metadata), deltas):
        parts.append(TIMED_FRAME_DESCRIPTOR.pack(start, count, delta))
    if name == "window":
        mean = summary.window_mean
        parts.append(struct.pack("<H", mean.shape[-1]))
        parts.append(np.stack([mean.imag, mean.real], axis=-1).astype("<f8").tobytes())
    parts.append(np.asarray(summary.loop_power[name]).astype("<f4").tobytes())
    samples = 2 * (metadata["chirps_per_frame"] // metadata["n_tx"]) * metadata["n_rx"]
    samples *= sum(count for _start, count in _frame_windows(metadata))
    parts.append(_SUMMARY_TRAILER.pack(summary.noise_power[name], samples, 0))
    return b"".join(parts)


def summary_reply_nbytes(partial: bytes) -> int | None:
    """Full length of a summary reply, or None until enough of it has arrived."""
    if len(partial) < _SUMMARY_HEADER.size:
        return None
    magic, scope, _n_tx, n_rx, _pad, loops, frames, bins, _period = _SUMMARY_HEADER.unpack_from(
        partial
    )
    if magic != SUMMARY_MAGIC or scope >= len(MTI_SCOPES):
        raise ValueError("not a readback summary")
    size = _SUMMARY_HEADER.size + TIMED_FRAME_DESCRIPTOR.size * frames
    if MTI_SCOPES[scope] == "window":
        if len(partial) < size + 2:
            return None
        (mean_bins,) = struct.unpack_from("<H", partial, size)
        size += 2 + 2 * n_rx * mean_bins * 16
    return size + 4 * frames * loops * bins + _SUMMARY_TRAILER.size


def _decode_summary_reply(reply: bytes) -> tuple[str, dict, np.ndarray, float, np.ndarray | None]:
    if summary_reply_nbytes(reply) != len(reply):
        raise ValueError("truncated readback summary")
    _magic, scope, n_tx, n_rx, _pad, loops, frames, bins, period = _SUMMARY_HEADER.unpack_from(
        reply
    )
    position = _SUMMARY_HEADER.size
    descriptors = list(TIMED_FRAME_DESCRIPTOR.iter_unpack(reply[position : position + 4 * frames]))
    position += TIMED_FRAME_DESCRIPTOR.size * frames
    offsets = np.cumsum([delta for _start, _count, delta in descriptors])
    metadata = {
        "version": _READBACK_DUMP_VERSION,
        "sample_fmt": SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        "n_frames": frames,
        "chirps_per_frame": loops * n_tx,
        "n_tx": n_tx,
        "n_rx": n_rx,
        "n_samples": bins,
        "trigger_frame": 0,
        "frame_period_us": period,
        "range_bin_starts": tuple(start for start, _count, _delta in descriptors),
        "range_bin_counts": tuple(count for _start, count, _delta in descriptors),
        "frame_time_offsets_us": tuple(int(offset) for offset in offsets),
    }
    mean = None
    if MTI_SCOPES[scope] == "window":
        (mean_bins,) = struct.unpack_from("<H", reply, position)
        words = np.frombuffer(reply, "<f8", 2 * n_rx * mean_bins * 2, position + 2)
        words = words.reshape(2, n_rx, mean_bins, 2)
        mean = words[..., 1] + 1j * words[..., 0]
        position += 2 + words.nbytes
    power = np.frombuffer(reply, "<f4", frames * loops * bins, position).astype(float)
    noise, _samples, status = _SUMMARY_TRAILER.unpack_from(reply, position + 4 * power.size)
    if status != 0:
        raise ValueError(f"radar could not summarize the capture (status {status})")
    return MTI_SCOPES[scope], metadata, power.reshape(frames * loops, bins), noise, mean


def decode_summary(*replies: bytes) -> CaptureSummary:
    """Build a summary from the burst reply and, when fetched, the window reply."""
    loop_power: dict[str, np.ndarray] = {}
    noise_power: dict[str, float] = {}
    metadata: dict = {}
    window_mean = None
    for reply in replies:
        scope, reply_metadata, power, noise, mean = _decode_summary_reply(reply)
        if metadata and reply_metadata != metadata:
            raise ValueError("readback summaries describe different captures")
        metadata = reply_metadata
        loop_power[scope] = power
        noise_power[scope] = noise
        window_mean = mean if mean is not None else window_mean
    if "burst" not in loop_power:
        raise ValueError("readback needs the burst summary")
    if window_mean is None:
        window_mean = np.zeros((2, metadata["n_rx"], 0), dtype=complex)
    return CaptureSummary(metadata, loop_power, noise_power, window_mean)


def encode_windows_request(windows: tuple[Window, ...]) -> str:
    """Argument of `l3bins`: four hex digits per frame (first local bin, count)."""
    return "".join(f"{low:02x}{count:02x}" for low, count in windows)


def windows_reply_nbytes(summary: CaptureSummary, windows: tuple[Window, ...]) -> int:
    """Length of the `l3bins` reply for these windows."""
    return _WINDOWS_HEADER.size + 2 * len(windows) + windows_nbytes(summary, windows)


def encode_windows_reply(windows: tuple[Window, ...], samples: tuple[np.ndarray, ...]) -> bytes:
    """Reference for the firmware's `l3bins` reply."""
    parts = [_WINDOWS_HEADER.pack(WINDOWS_MAGIC, len(windows))]
    parts.extend(bytes(window) for window in windows)
    for (_low, count), frame_samples in zip(windows, samples):
        if count:
            words = np.stack([frame_samples.imag, frame_samples.real], axis=-1)
            parts.append(np.rint(words).astype("<i2").tobytes())
    return b"".join(parts)


def decode_windows_reply(
    reply: bytes, summary: CaptureSummary, windows: tuple[Window, ...]
) -> tuple[np.ndarray, ...]:
    """Each requested frame's [chirp, rx, bin] samples from an `l3bins` reply."""
    if len(reply) != windows_reply_nbytes(summary, windows):
        raise ValueError("truncated readback windows")
    magic, frames = _WINDOWS_HEADER.unpack_from(reply)
    position = _WINDOWS_HEADER.size + 2 * len(windows)
    echoed = reply[_WINDOWS_HEADER.size : position]
    if magic != WINDOWS_MAGIC or frames != len(windows) or echoed != bytes(sum(windows, ())):
        raise ValueError("radar answered a different window request")
    chirps, n_rx = summary.metadata["chirps_per_frame"], summary.metadata["n_rx"]
    samples = []
    for _low, count in windows:
        words = np.frombuffer(reply, "<i2", chirps * n_rx * count * 2, position)
        words = words.reshape(chirps, n_rx, count, 2).astype(float)
        samples.append(words[..., 1] + 1j * words[..., 0])
        position += 2 * words.size
    return tuple(samples)


@dataclass(frozen=True)
class Readback:
    """One capture's summary and the range windows fetched for its ball."""

    summary: CaptureSummary
    windows: tuple[Window, ...]
    samples: tuple[np.ndarray, ...]

    def prepare(self) -> PreparedLCMFCapture:
        """Estimator-ready capture holding only what was fetched."""
        return prepare_readback(self.summary, self.windows, self.samples)

    def covers(self, *, club: str | None, net_range_m: float | None) -> bool:
        """Whether the estimator, run for this club, stays inside what was fetched."""
        vertical = prepare_readback(self.summary, (), ()).vertical
        if "window" in self.summary.loop_power:
            track, _notch_used = select_ball_track(vertical, club=club, net_range_m=net_range_m)
        else:
            track = find_scope_track(vertical, club=club, net_range_m=net_range_m)
            if not burst_track_settled(track):
                return False
        if track is None:
            return False
        needed = track_windows(self.summary, track, margin_bins=1)
        return all(
            count == 0 or (have_low <= low and low + count <= have_low + have_count)
            for (low, count), (have_low, have_count) in zip(needed, self.windows)
        )


class ReadbackLink(Protocol):
    """The radar commands a selective readback uses on a frozen capture."""

    def read_summary(self, scope: int) -> bytes:
        """Reply to `l3sum <scope>`."""

    def read_windows(self, request: str, nbytes: int) -> bytes:
        """Reply to `l3bins <request>`, which is `nbytes` long."""


def read_selective(
    link: ReadbackLink,
    *,
    club: str | None = None,
    net_range_m: float | None = None,
    margin_bins: int = DEFAULT_MARGIN_BINS,
) -> Readback | None:
    """Fetch the ball's samples from a frozen capture; None when no ball shows."""
    burst_reply = link.read_summary(0)
    summary = decode_summary(burst_reply)
    vertical = prepare_readback(summary, (), ()).vertical
    track = find_scope_track(vertical, club=club, net_range_m=net_range_m)
    if not burst_track_settled(track):
        summary = decode_summary(burst_reply, link.read_summary(1))
        vertical = prepare_readback(summary, (), ()).vertical
        track, _notch_used = select_ball_track(vertical, club=club, net_range_m=net_range_m)
    if track is None:
        return None
    windows = track_windows(summary, track, margin_bins=margin_bins)
    reply = link.read_windows(
        encode_windows_request(windows), windows_reply_nbytes(summary, windows)
    )
    return Readback(summary, windows, decode_windows_reply(reply, summary, windows))
