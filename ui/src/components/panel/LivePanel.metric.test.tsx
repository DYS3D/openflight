import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../../types/shot';

const shot = {
  ball_speed_mph: 92,
  club_speed_mph: 68,
  smash_factor: 1.35,
  estimated_carry_yards: 210,
  carry_range: [205, 215],
  club: 'driver',
  timestamp: '2026-08-19T10:00:00Z',
  peak_magnitude: 100,
  launch_angle_vertical: 13.4,
  launch_angle_horizontal: -1.2,
  launch_angle_confidence: 0.8,
  angle_source: 'radar',
  club_angle_deg: 2.1,
  club_path_deg: -0.6,
  spin_axis_deg: 3.4,
  spin_rpm: 2650,
  spin_confidence: 0.9,
  spin_quality: 'high',
  spin_source: 'measured',
  carry_spin_adjusted: 214,
  carry_normalized_yards: 200,
  profile_id: 'james',
} satisfies Shot;

describe('LivePanel normalized carry with metric units', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('converts the normalized carry like the carry value', async () => {
    const storage: Record<string, string> = { 'openflight.unit-system': 'metric' };
    vi.stubGlobal('window', { localStorage: { getItem: (key: string) => storage[key] ?? null, setItem: () => {} } });
    vi.resetModules();
    const { renderToString } = await import('react-dom/server');
    const { LivePanel } = await import('./LivePanel');

    const html = renderToString(
      <LivePanel shot={shot} shots={[shot]} profileId="james" profileName="James" clubLabel="DR" showNormalizedCarry />
    ).replace(/<!-- -->/g, '');

    expect(html).toContain('>196<');
    expect(html).toContain('Normalized 183 m');
  });
});
