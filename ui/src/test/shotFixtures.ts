import type { Shot, ShotFlight } from '../types/shot';

export function makeTestShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 120,
    club_speed_mph: 88,
    smash_factor: 1.36,
    estimated_carry_yards: 160,
    carry_range: [155, 165],
    club: '7-iron',
    profile_id: 'james',
    profile_name: 'James',
    timestamp: '2026-09-30T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 18,
    launch_angle_horizontal: 0,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 6000,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: null,
    ...overrides,
  };
}

export function makeTestFlight(carry: number, lateral = 0, apex = carry / 6): ShotFlight {
  return {
    points: [
      [0, 0, 0],
      [carry * 0.55, lateral * 0.4, apex],
      [carry, lateral, 0],
    ],
    carry_yards: carry,
    lateral_yards: lateral,
    apex_yards: apex,
    landing_angle_deg: 45,
    flight_time_s: 6,
  };
}

export function makeTestSession(club: string, carries: number[], extra: Partial<Shot> = {}, start = 0): Shot[] {
  return carries.map((carry, index) =>
    makeTestShot({
      club,
      estimated_carry_yards: carry,
      timestamp: `2026-09-30T10:${String(start + index).padStart(2, '0')}:00Z`,
      ...extra,
    })
  );
}
