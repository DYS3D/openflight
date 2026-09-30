import type { RadarHealth } from '../types/socket';

/** How far the noise floor sits above the radar's quiet baseline, in dB. */
export function radarNoiseDelta(health: Pick<RadarHealth, 'noise_floor_db' | 'baseline_db'>): number {
  return health.noise_floor_db - health.baseline_db;
}

/** Signed one-decimal dB delta, e.g. "+3.5" or "-0.2". */
export function formatNoiseDelta(health: Pick<RadarHealth, 'noise_floor_db' | 'baseline_db'>): string {
  const delta = radarNoiseDelta(health);
  return `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}`;
}
