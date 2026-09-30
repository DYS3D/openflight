import { describe, expect, it } from 'vitest';
import type { Shot } from '../../types/shot';
import { consistencyBand, consistencyBands } from './consistency';

const HISTORY = [90, 90, 100, 110, 110];
const MEAN = 100;
const SD = 10;

describe('consistencyBand', () => {
  it('is good within 1 SD, fair within 2 SD, poor beyond', () => {
    expect(consistencyBand(MEAN, HISTORY)).toBe('good');
    expect(consistencyBand(MEAN + SD, HISTORY)).toBe('good');
    expect(consistencyBand(MEAN - SD * 1.01, HISTORY)).toBe('fair');
    expect(consistencyBand(MEAN + SD * 2, HISTORY)).toBe('fair');
    expect(consistencyBand(MEAN + SD * 2.01, HISTORY)).toBe('poor');
    expect(consistencyBand(MEAN - SD * 3, HISTORY)).toBe('poor');
  });

  it('needs at least five earlier values', () => {
    expect(consistencyBand(100, HISTORY.slice(0, 4))).toBeNull();
  });

  it('treats a perfectly repeatable history as a zero-width band', () => {
    expect(consistencyBand(1.45, [1.45, 1.45, 1.45, 1.45, 1.45])).toBe('good');
    expect(consistencyBand(1.46, [1.45, 1.45, 1.45, 1.45, 1.45])).toBe('poor');
  });
});

let clock = 0;
function makeShot(overrides: Partial<Shot> = {}): Shot {
  clock += 1;
  return {
    ball_speed_mph: 100,
    club_speed_mph: 75,
    smash_factor: 1.4,
    estimated_carry_yards: 150,
    carry_range: [145, 155],
    club: '7-iron',
    timestamp: `t${String(clock).padStart(4, '0')}`,
    peak_magnitude: 100,
    launch_angle_vertical: 18,
    launch_angle_horizontal: 0,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: null,
    spin_confidence: null,
    spin_quality: null,
    spin_source: null,
    carry_spin_adjusted: null,
    profile_id: 'james',
    ...overrides,
  };
}

function historyShots(overrides: Partial<Shot> = {}): Shot[] {
  return HISTORY.map((ballSpeed) => makeShot({ ball_speed_mph: ballSpeed, ...overrides }));
}

describe('consistencyBands', () => {
  it('bands carry, ball speed, smash and V. launch against five earlier same-club shots', () => {
    const current = makeShot({ ball_speed_mph: MEAN + SD * 3 });
    const bands = consistencyBands(current, [...historyShots(), current], 'james');

    expect(bands).toEqual({ carry: 'good', ball_speed: 'poor', smash: 'good', launch_v: 'good' });
  });

  it('gives no bands until the club has five earlier shots for this profile', () => {
    const current = makeShot();
    const shots = [...historyShots().slice(0, 4), current];

    expect(consistencyBands(current, shots, 'james')).toEqual({});
  });

  it("ignores other profiles, other clubs, and the shot's own and later entries", () => {
    const current = makeShot({ ball_speed_mph: 100 });
    const shots = [
      ...historyShots().slice(0, 4),
      makeShot({ profile_id: 'alex', ball_speed_mph: 100 }),
      makeShot({ club: 'driver', ball_speed_mph: 100 }),
      current,
      makeShot({ ball_speed_mph: 100 }),
    ];

    expect(consistencyBands(current, shots, 'james')).toEqual({});
  });

  it('compares the carry the tile shows (spin-adjusted when present)', () => {
    const history = historyShots({ carry_spin_adjusted: 150 });
    const current = makeShot({ estimated_carry_yards: 150, carry_spin_adjusted: 170 });

    expect(consistencyBands(current, [...history, current], 'james').carry).toBe('poor');
  });

  it('skips a metric the shot or its history lacks', () => {
    const history = historyShots({ launch_angle_vertical: null });
    const current = makeShot({ smash_factor: null });
    const bands = consistencyBands(current, [...history, current], 'james');

    expect(bands).not.toHaveProperty('launch_v');
    expect(bands).not.toHaveProperty('smash');
    expect(bands).toHaveProperty('ball_speed');
  });

  it('never bands swing-speed reps', () => {
    const history = historyShots({ mode: 'swing-speed' });
    const current = makeShot({ mode: 'swing-speed' });

    expect(consistencyBands(current, [...history, current], 'james')).toEqual({});
  });
});
