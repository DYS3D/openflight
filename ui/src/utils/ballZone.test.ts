import { describe, expect, it } from 'vitest';
import { ballZoneRect, DEFAULT_BALL_ZONE_SIZE_PCT } from './ballZone';

describe('ballZoneRect', () => {
  it('centres a 12% box on the alignment point', () => {
    expect(ballZoneRect({ alignment_x_pct: 48, alignment_y_pct: 55 })).toEqual({
      left: 42,
      top: 49,
      width: 12,
      height: 12,
      centerX: 48,
      centerY: 55,
    });
  });

  it('falls back to the fixed ball guide when the settings carry no alignment', () => {
    const rect = ballZoneRect({});
    expect(rect.centerX).toBe(50);
    expect(rect.centerY).toBe(78);
    expect(rect.width).toBe(DEFAULT_BALL_ZONE_SIZE_PCT);
  });

  it('slides the box inward at the frame edge instead of clipping it', () => {
    const rect = ballZoneRect({ alignment_x_pct: 2, alignment_y_pct: 99 }, 20);
    expect(rect.left).toBe(0);
    expect(rect.top).toBe(80);
    expect(rect.centerX).toBe(10);
    expect(rect.centerY).toBe(90);
  });

  it('accepts a custom size', () => {
    const rect = ballZoneRect({ alignment_x_pct: 50, alignment_y_pct: 50 }, 30);
    expect(rect).toMatchObject({ left: 35, top: 35, width: 30, height: 30 });
  });
});
