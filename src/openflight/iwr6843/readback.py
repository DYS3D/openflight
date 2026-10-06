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

from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.dump import is_range_snapshot, pack_dump, parse_dump
from openflight.iwr6843.lcmf import PreparedLCMFCapture, prepare_lcmf_capture
from openflight.iwr6843.shot import select_ball_track
from openflight.iwr6843.tracking import BallTrack

MTI_SCOPES = ("burst", "window")
# The estimator samples loops this far outside the fitted track span.
TRACK_TIME_PAD_S = 2e-3
# Bins fetched beyond the track on each side; the estimator needs one.
DEFAULT_MARGIN_BINS = 2

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
    window_mti = np.zeros_like(range_movie)
    frame_windows = _frame_windows(metadata)
    for frame, (low, count) in enumerate(windows):
        first = frame_windows[frame][0] + low
        window_mti[frame, :, :, :, low : low + count] = (
            range_movie[frame, :, :, :, low : low + count]
            - summary.window_mean[:, None, :, first : first + count]
        )
    # pylint: disable=protected-access  # seeding the capture's own lazy caches
    vertical._mti_by_scope["window"] = window_mti
    vertical._power_by_scope.update(summary.loop_power)
    vertical._noise_by_scope.update(summary.noise_power)
    return prepared
