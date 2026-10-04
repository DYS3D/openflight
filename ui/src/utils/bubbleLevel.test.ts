import { describe, expect, it } from 'vitest';
import { bubblePosition, bubbleRangeDeg, formatLevelDegrees } from './bubbleLevel';

describe('bubblePosition', () => {
  it('centres the bubble and reports level at zero tilt', () => {
    expect(bubblePosition(0, 0, 1)).toEqual({ x: 0, y: 0, level: true });
  });

  it('moves the bubble to the high side: left for positive roll, up for positive pitch', () => {
    // Positive roll is the right side down, so the bubble floats left.
    const position = bubblePosition(2, 1, 1);
    expect(position.x).toBeCloseTo(-25);
    expect(position.y).toBeCloseTo(-50);
    expect(position.level).toBe(false);
  });

  it('treats readings at the threshold as level and just beyond it as not', () => {
    expect(bubblePosition(1, -1, 1).level).toBe(true);
    expect(bubblePosition(1.01, 0, 1).level).toBe(false);
    expect(bubblePosition(0, -1.2, 1).level).toBe(false);
  });

  it('clamps wild readings to the rim without changing their direction', () => {
    const position = bubblePosition(-30, 40, 1);
    expect(Math.hypot(position.x, position.y)).toBeCloseTo(100);
    expect(position.x).toBeLessThan(0);
    expect(position.y).toBeGreaterThan(0);
    expect(position.x / position.y).toBeCloseTo(-40 / 30);
  });

  it('scales the vial to the threshold but never tighter than two degrees', () => {
    expect(bubbleRangeDeg(0.5)).toBe(2);
    expect(bubbleRangeDeg(1)).toBe(4);
    expect(bubbleRangeDeg(2.5)).toBe(10);
  });
});

describe('formatLevelDegrees', () => {
  it('prints one decimal and never a negative zero', () => {
    expect(formatLevelDegrees(0)).toBe('0.0');
    expect(formatLevelDegrees(-0.04)).toBe('0.0');
    expect(formatLevelDegrees(-0.06)).toBe('-0.1');
    expect(formatLevelDegrees(1.25)).toBe('1.3');
  });
});
