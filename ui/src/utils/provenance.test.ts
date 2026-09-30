import { describe, expect, it } from 'vitest';
import type { Shot } from '../types/shot';
import { isHorizontalLaunchEstimated, isSpinEstimated, isVerticalLaunchEstimated } from './provenance';

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 140,
    club_speed_mph: 100,
    smash_factor: 1.4,
    estimated_carry_yards: 230,
    carry_range: [220, 240],
    club: 'driver',
    timestamp: '2026-09-30T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 12,
    launch_angle_horizontal: 0,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 2600,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 235,
    ...overrides,
  };
}

describe('shot provenance', () => {
  it('uses per-axis sources ahead of the combined angle_source', () => {
    const shot = makeShot({
      angle_source: 'radar',
      launch_angle_vertical_source: 'radar',
      launch_angle_horizontal_source: 'estimated',
    });
    expect(isVerticalLaunchEstimated(shot)).toBe(false);
    expect(isHorizontalLaunchEstimated(shot)).toBe(true);
  });

  it('falls back to angle_source for payloads without per-axis sources', () => {
    const shot = makeShot({ angle_source: 'estimated' });
    expect(isVerticalLaunchEstimated(shot)).toBe(true);
    expect(isHorizontalLaunchEstimated(shot)).toBe(true);
  });

  it('treats mock angles as estimated', () => {
    expect(isVerticalLaunchEstimated(makeShot({ launch_angle_vertical_source: 'mock' }))).toBe(true);
  });

  it('never flags a missing value', () => {
    const shot = makeShot({ launch_angle_vertical: null, launch_angle_horizontal: null, angle_source: 'estimated' });
    expect(isVerticalLaunchEstimated(shot)).toBe(false);
    expect(isHorizontalLaunchEstimated(shot)).toBe(false);
    expect(isSpinEstimated(makeShot({ spin_rpm: null, spin_source: 'calculated' }))).toBe(false);
  });

  it('treats mock data from older payloads as estimated', () => {
    const shot = makeShot({ angle_source: 'mock', spin_source: 'mock' });
    expect(isVerticalLaunchEstimated(shot)).toBe(true);
    expect(isHorizontalLaunchEstimated(shot)).toBe(true);
    expect(isSpinEstimated(shot)).toBe(true);
  });

  it('flags kinematically calculated spin', () => {
    expect(isSpinEstimated(makeShot({ spin_source: 'calculated' }))).toBe(true);
    expect(isSpinEstimated(makeShot({ spin_source: 'measured' }))).toBe(false);
  });
});
