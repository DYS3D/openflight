"""Enclosure roll: level-frame angle correction and the level warning."""

from __future__ import annotations

import math

# The warning clears only once tilt falls below this fraction of the threshold.
LEVEL_CLEAR_FRACTION = 0.8


def level_frame_angles(
    vertical_deg: float,
    horizontal_deg: float,
    roll_deg: float,
) -> tuple[float, float]:
    """Rotate radar-frame launch angles by enclosure roll into a level frame.

    The direction is rotated about the enclosure's downrange axis. Roll is
    positive right side down (viewed from behind the unit); horizontal is
    positive right. For small angles the result is approximately
    ``(vertical - roll * horizontal, horizontal + roll * vertical)`` with
    roll in radians.
    """
    vertical = math.radians(vertical_deg)
    horizontal = math.radians(horizontal_deg)
    roll = math.radians(roll_deg)
    x = math.cos(vertical) * math.cos(horizontal)
    y = math.cos(vertical) * math.sin(horizontal)
    z = math.sin(vertical)
    level_y = y * math.cos(roll) + z * math.sin(roll)
    level_z = -y * math.sin(roll) + z * math.cos(roll)
    return (
        math.degrees(math.asin(max(-1.0, min(1.0, level_z)))),
        math.degrees(math.atan2(level_y, x)),
    )


class LevelMonitor:
    """Whether the enclosure is level, with hysteresis so the state does not flap.

    The rig becomes unlevel once pitch or roll exceeds ``threshold_deg`` and
    is level again only after both fall below ``LEVEL_CLEAR_FRACTION`` of it.
    """

    def __init__(self, threshold_deg: float):
        if threshold_deg <= 0:
            raise ValueError("threshold_deg must be positive")
        self.threshold_deg = threshold_deg
        self._status: dict | None = None

    @property
    def status(self) -> dict | None:
        """The latest ``level_status`` payload, or None before the first reading."""
        return None if self._status is None else dict(self._status)

    def update(self, pitch_deg: float, roll_deg: float) -> bool:
        """Record one orientation; return True when the level state changed."""
        tilt = max(abs(pitch_deg), abs(roll_deg))
        previous = None if self._status is None else self._status["level"]
        if previous is False:
            level = tilt < LEVEL_CLEAR_FRACTION * self.threshold_deg
        else:
            level = tilt <= self.threshold_deg
        self._status = {
            "pitch_deg": round(pitch_deg, 2),
            "roll_deg": round(roll_deg, 2),
            "level": level,
            "threshold_deg": self.threshold_deg,
        }
        return level != previous
