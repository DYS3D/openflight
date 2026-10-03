import { describe, expect, it } from 'vitest';
import { clubWindows, rangeStatus } from './launchWindows';

describe('rangeStatus', () => {
  it('rates driver launch against the 10–15° window', () => {
    expect(rangeStatus('launch_v', 8.9, 'driver')).toBe('low');
    expect(rangeStatus('launch_v', 12, 'driver')).toBe('in');
    expect(rangeStatus('launch_v', 16, 'driver')).toBe('high');
  });

  it('rates driver spin against the 2,000–2,800 rpm window', () => {
    expect(rangeStatus('spin', 1800, 'driver')).toBe('low');
    expect(rangeStatus('spin', 2313, 'driver')).toBe('in');
    expect(rangeStatus('spin', 3400, 'driver')).toBe('high');
  });

  it('centres other clubs on their tour average', () => {
    const windows = clubWindows('7-iron');
    expect(windows?.launch.min).toBeCloseTo(13.8);
    expect(windows?.launch.max).toBeCloseTo(18.8);
    expect(windows?.spin).toEqual({ min: 6032, max: 8162 });
    expect(rangeStatus('spin', 7100, '7-iron')).toBe('in');
  });

  it('gives no rating for unknown clubs, missing values, or other metrics', () => {
    expect(rangeStatus('spin', 2500, 'Swing Speed')).toBeNull();
    expect(rangeStatus('spin', null, 'driver')).toBeNull();
    expect(rangeStatus('club_speed', 100, 'driver')).toBeNull();
  });
});
