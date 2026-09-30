import type { Shot } from '../../types/shot';
import { isSwingSpeedShot } from '../../types/shot';
import { liveCarryYards } from './liveMetrics';

export type ConsistencyBand = 'good' | 'fair' | 'poor';

export const MIN_CONSISTENCY_HISTORY = 5;

const TINTED_METRICS: Record<string, (shot: Shot) => number | null> = {
  carry: liveCarryYards,
  ball_speed: (shot) => shot.ball_speed_mph,
  smash: (shot) => shot.smash_factor,
  launch_v: (shot) => shot.launch_angle_vertical,
};

/** Within 1 sample SD of the history's mean is good, within 2 fair, beyond that poor. */
export function consistencyBand(value: number, history: number[]): ConsistencyBand | null {
  if (history.length < MIN_CONSISTENCY_HISTORY) {
    return null;
  }

  const mean = history.reduce((sum, x) => sum + x, 0) / history.length;
  const sd = Math.sqrt(history.reduce((sum, x) => sum + (x - mean) ** 2, 0) / (history.length - 1));
  const deviation = Math.abs(value - mean);
  if (deviation <= sd) return 'good';
  if (deviation <= 2 * sd) return 'fair';
  return 'poor';
}

/** Bands keyed by Live metric id, against this profile's earlier same-club shots. */
export function consistencyBands(shot: Shot, shots: Shot[], profileId: string): Record<string, ConsistencyBand> {
  if (isSwingSpeedShot(shot)) {
    return {};
  }

  const index = shots.findIndex((candidate) => candidate.timestamp === shot.timestamp);
  const earlier = index === -1 ? shots : shots.slice(0, index);
  const history = earlier.filter(
    (candidate) => candidate.profile_id === profileId && candidate.club === shot.club && !isSwingSpeedShot(candidate)
  );
  if (history.length < MIN_CONSISTENCY_HISTORY) {
    return {};
  }

  const bands: Record<string, ConsistencyBand> = {};
  for (const [id, read] of Object.entries(TINTED_METRICS)) {
    const value = read(shot);
    if (value === null) continue;
    const band = consistencyBand(
      value,
      history.map(read).filter((past): past is number => past !== null)
    );
    if (band) bands[id] = band;
  }
  return bands;
}
