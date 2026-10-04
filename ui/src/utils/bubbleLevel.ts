import type { MessageKey } from '../i18n';
import type { LevelStatus } from '../types/socket';
/** Bubble offset in percent of the vial radius (100 = at the rim), plus level state. */
export interface BubblePosition {
  /** Horizontal offset: positive roll (right side down) moves the bubble left, to the high side. */
  x: number;
  /** Vertical offset: positive pitch (nose up) moves the bubble up. */
  y: number;
  /** True when both axes are within the threshold. */
  level: boolean;
}

/** Degrees that put the bubble at the rim: the threshold times this factor, never under 2°. */
const RIM_THRESHOLD_MULTIPLE = 4;
const MIN_RIM_DEG = 2;

/** Degrees of tilt that place the bubble on the vial rim. */
export function bubbleRangeDeg(thresholdDeg: number): number {
  return Math.max(MIN_RIM_DEG, thresholdDeg * RIM_THRESHOLD_MULTIPLE);
}

/**
 * Where to draw the bubble for a pitch/roll pair. The bubble moves opposite
 * to gravity, like a real vial: tilt the nose up and the bubble rises; drop
 * the right side and it drifts left. The
 * offset is clamped to the rim so wild readings still draw inside the circle.
 */
export function bubblePosition(pitchDeg: number, rollDeg: number, thresholdDeg: number): BubblePosition {
  const range = bubbleRangeDeg(thresholdDeg);
  const rawX = rollDeg === 0 ? 0 : (-rollDeg / range) * 100;
  const rawY = pitchDeg === 0 ? 0 : (-pitchDeg / range) * 100;
  const distance = Math.hypot(rawX, rawY);
  const scale = distance > 100 ? 100 / distance : 1;
  return {
    x: rawX * scale,
    y: rawY * scale,
    level: Math.abs(pitchDeg) <= thresholdDeg && Math.abs(rollDeg) <= thresholdDeg,
  };
}

/** Signed one-decimal readout, so 0 shows as "0.0" and -0.04 is not "-0.0". */
export function formatLevelDegrees(deg: number): string {
  const rounded = Math.round(deg * 10) / 10;
  const text = Math.abs(rounded).toFixed(1);
  return rounded < 0 ? `-${text}` : text;
}

// Matches the server's hysteresis: an axis is fine once under 80% of the threshold.
const HINT_FRACTION = 0.8;

/**
 * Which corner foot to raise on the four-foot enclosure. Positive pitch is the
 * front (target side) high; positive roll is the right side low, looking down
 * the target line from behind. Raising the lowest corner fixes both axes at
 * once; with one axis off, both feet on the low side go up.
 */
export function levelHint(status: LevelStatus): MessageKey | null {
  const limit = status.threshold_deg * HINT_FRACTION;
  const pitchOff = Math.abs(status.pitch_deg) >= limit;
  const rollOff = Math.abs(status.roll_deg) >= limit;
  const end = status.pitch_deg > 0 ? 'Back' : 'Front';
  const side = status.roll_deg > 0 ? 'Right' : 'Left';
  if (pitchOff && rollOff) return `level.raise${end}${side}` as MessageKey;
  if (pitchOff) return `level.raise${end}` as MessageKey;
  if (rollOff) return `level.raise${side}` as MessageKey;
  return null;
}
