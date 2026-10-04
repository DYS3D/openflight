import type { Shot, SwingLength } from '../types/shot';
import { ballShots, carryYards } from './shotAnalysis';

export const SWING_LENGTHS: readonly SwingLength[] = ['full', '3/4', '1/2'];
export const WEDGES = ['pw', 'gw', 'sw', 'lw'] as const;

export interface WedgeCell {
  median: number | null;
  shots: number;
}

export interface WedgeRow {
  club: string;
  cells: Record<SwingLength, WedgeCell>;
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** Median carry per wedge and swing length, from the shots tagged with a swing length. */
export function buildWedgeMatrix(shots: readonly Shot[]): WedgeRow[] {
  const tagged = ballShots(shots).filter(
    (shot) => shot.swing_length && (WEDGES as readonly string[]).includes(shot.club)
  );
  return WEDGES.map((club) => {
    const clubShots = tagged.filter((shot) => shot.club === club);
    const cells = Object.fromEntries(
      SWING_LENGTHS.map((swing) => {
        const carries = clubShots.filter((shot) => shot.swing_length === swing).map(carryYards);
        return [swing, { median: median(carries), shots: carries.length }];
      })
    ) as Record<SwingLength, WedgeCell>;
    return { club, cells };
  });
}
