"""Shared back-off schedule for radar auto-reconnect (``--radar-auto-reconnect``).

Both serial radars (OPS243 rolling buffer, IWR6843 L3 dump) re-run their
port detection after a serial error with the same capped exponential
back-off so a pulled USB cable is polled every 1, 2, 4, ... 30 s.
"""

from __future__ import annotations

RECONNECT_BACKOFF_INITIAL_S = 1.0
RECONNECT_BACKOFF_MAX_S = 30.0

RADAR_STATE_CONNECTED = "connected"
RADAR_STATE_RECONNECTING = "reconnecting"


def reconnect_backoff_s(failed_attempts: int) -> float:
    """Seconds to wait after ``failed_attempts`` consecutive reconnect failures.

    Doubles from ``RECONNECT_BACKOFF_INITIAL_S`` and never exceeds
    ``RECONNECT_BACKOFF_MAX_S``.
    """
    exponent = max(0, failed_attempts - 1)
    return min(RECONNECT_BACKOFF_MAX_S, RECONNECT_BACKOFF_INITIAL_S * (2**exponent))


__all__ = [
    "RADAR_STATE_CONNECTED",
    "RADAR_STATE_RECONNECTING",
    "RECONNECT_BACKOFF_INITIAL_S",
    "RECONNECT_BACKOFF_MAX_S",
    "reconnect_backoff_s",
]
