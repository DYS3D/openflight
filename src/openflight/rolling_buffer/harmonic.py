"""Harmonic-series spin estimation for balls with a radar marker pattern.

A marker pattern (Titleist RCT) repeats once per revolution but is not a
single dot, so its envelope carries a whole harmonic series and the strongest
line is often 2x or 3x spin. Fitting the series as one model lets the odd
harmonics settle which line is the fundamental.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DETREND_POLY_ORDER = 3
LOWPASS_ORDER = 4
# The lowpass passes the top harmonic with this margin.
LOWPASS_MARGIN = 1.1
# Samples kept per lowpass cycle when decimating before the scan.
DECIMATED_SAMPLES_PER_CYCLE = 4


@dataclass(frozen=True)
class HarmonicEstimate:
    """Best-fitting spin and how much of the envelope its harmonics explain."""

    spin_hz: float
    spin_rpm: float
    # Explained envelope variance, penalised for the number of harmonics.
    fit: float
    explained: float
    harmonics: int


def estimate_harmonic_spin(
    envelope: np.ndarray,
    sample_rate_hz: float,
    *,
    spin_rpm_band: tuple[float, float] = (2000.0, 12_000.0),
    max_harmonic_hz: float = 650.0,
    min_cycles: float = 2.0,
    step_hz: float = 0.5,
) -> HarmonicEstimate:
    """Fit ``spin, 2 x spin, ...`` up to ``max_harmonic_hz`` and return the best spin.

    Half the true spin always fits at least as well with twice the harmonics,
    so candidates are ranked by adjusted R^2 over the envelope's independent
    samples (2 x bandwidth x duration).
    """
    values = np.asarray(envelope, dtype=np.float64)
    duration_s = len(values) / sample_rate_hz
    low_hz = max(spin_rpm_band[0] / 60.0, min_cycles / duration_s)
    high_hz = spin_rpm_band[1] / 60.0
    if low_hz >= high_hz:
        raise ValueError(
            f"window of {duration_s * 1000:.0f} ms is too short for {min_cycles:g} spin cycles"
        )

    from scipy.signal import butter, sosfiltfilt  # pylint: disable=import-outside-toplevel

    index = np.arange(len(values), dtype=np.float64)
    values = values - np.polyval(np.polyfit(index, values, DETREND_POLY_ORDER), index)
    bandwidth_hz = LOWPASS_MARGIN * max_harmonic_hz
    sos = butter(LOWPASS_ORDER, bandwidth_hz / (sample_rate_hz / 2), output="sos")
    values = sosfiltfilt(sos, values)
    stride = max(1, int(sample_rate_hz // (DECIMATED_SAMPLES_PER_CYCLE * bandwidth_hz)))
    values = values[::stride]
    time_s = np.arange(len(values)) * stride / sample_rate_hz
    total = float(np.sum(values**2))
    if total <= 0:
        raise ValueError("envelope has no variation")

    independent_samples = 2.0 * bandwidth_hz * duration_s
    best: HarmonicEstimate | None = None
    for spin_hz in np.arange(low_hz, high_hz, step_hz):
        harmonics = int(max_harmonic_hz // spin_hz)
        spare = independent_samples - 2 * harmonics - 1
        if spare <= 0:
            continue
        angles = 2.0 * np.pi * spin_hz * np.outer(time_s, np.arange(1, harmonics + 1))
        design = np.hstack([np.cos(angles), np.sin(angles)])
        coefficients = np.linalg.lstsq(design, values, rcond=None)[0]
        explained = 1.0 - float(np.sum((values - design @ coefficients) ** 2)) / total
        fit = 1.0 - (1.0 - explained) * (independent_samples - 1) / spare
        if best is None or fit > best.fit:
            best = HarmonicEstimate(
                spin_hz=float(spin_hz),
                spin_rpm=float(spin_hz) * 60.0,
                fit=fit,
                explained=explained,
                harmonics=harmonics,
            )
    if best is None:
        raise ValueError(f"window of {duration_s * 1000:.0f} ms is too short for a harmonic fit")
    return best
