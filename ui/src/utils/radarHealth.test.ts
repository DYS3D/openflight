import { describe, expect, it } from 'vitest';
import { formatNoiseDelta, radarNoiseDelta } from './radarHealth';

describe('radarHealth', () => {
  it('measures the noise floor against the baseline', () => {
    expect(radarNoiseDelta({ noise_floor_db: -58.5, baseline_db: -62 })).toBeCloseTo(3.5);
  });

  it('formats the delta with an explicit sign', () => {
    expect(formatNoiseDelta({ noise_floor_db: -58.5, baseline_db: -62 })).toBe('+3.5');
    expect(formatNoiseDelta({ noise_floor_db: -62.2, baseline_db: -62 })).toBe('-0.2');
    expect(formatNoiseDelta({ noise_floor_db: -62, baseline_db: -62 })).toBe('+0.0');
  });
});
