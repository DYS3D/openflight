import type { Shot, ShotFlight } from '../types/shot';
import { carryYards, isSwingSpeedShot } from '../types/shot';
import {
  covarianceEllipse,
  fractionInsideEllipse,
  mean,
  sampleStdDev,
  type CovarianceEllipse,
  type Point2D,
} from './statistics';

export type ShotWithFlight = Shot & { flight: ShotFlight };

export { carryYards };

export function ballShots(shots: readonly Shot[]): Shot[] {
  return shots.filter((shot) => !isSwingSpeedShot(shot) && Number.isFinite(carryYards(shot)));
}

function isFiniteTriple(point: unknown): point is [number, number, number] {
  return Array.isArray(point) && point.length === 3 && point.every((value) => Number.isFinite(value));
}

export function isValidFlight(flight: ShotFlight | null | undefined): flight is ShotFlight {
  return Boolean(
    flight &&
    Array.isArray(flight.points) &&
    flight.points.length >= 2 &&
    flight.points.every(isFiniteTriple) &&
    Number.isFinite(flight.carry_yards) &&
    Number.isFinite(flight.lateral_yards)
  );
}

export function hasFlight(shot: Shot): shot is ShotWithFlight {
  return isValidFlight(shot.flight);
}

export const GHOST_COUNT = 5;

export interface FlightTraces {
  latest: ShotWithFlight;
  ghosts: ShotWithFlight[];
}

export function flightTraces(shots: readonly Shot[], ghostCount = GHOST_COUNT): FlightTraces | null {
  let latestIndex = -1;
  for (let index = shots.length - 1; index >= 0; index -= 1) {
    if (hasFlight(shots[index])) {
      latestIndex = index;
      break;
    }
  }
  if (latestIndex < 0) return null;

  const latest = shots[latestIndex] as ShotWithFlight;
  const ghosts: ShotWithFlight[] = [];
  for (let index = latestIndex - 1; index >= 0 && ghosts.length < ghostCount; index -= 1) {
    const shot = shots[index];
    if (shot.club === latest.club && hasFlight(shot)) ghosts.unshift(shot);
  }
  return { latest, ghosts };
}

export interface FlightExtent {
  downrange: number;
  height: number;
  lateral: number;
}

export function flightExtent(flights: readonly ShotFlight[]): FlightExtent {
  const extent: FlightExtent = { downrange: 0, height: 0, lateral: 0 };
  for (const flight of flights) {
    extent.downrange = Math.max(extent.downrange, flight.carry_yards);
    extent.lateral = Math.max(extent.lateral, Math.abs(flight.lateral_yards));
    for (const [x, y, z] of flight.points) {
      extent.downrange = Math.max(extent.downrange, x);
      extent.height = Math.max(extent.height, z);
      extent.lateral = Math.max(extent.lateral, Math.abs(y));
    }
  }
  return extent;
}

export type SeriesShape = 'circle' | 'square' | 'triangle' | 'diamond';

const SERIES_COLOURS = 8;
const SERIES_SHAPES: readonly SeriesShape[] = ['circle', 'square', 'triangle', 'diamond'];

export interface ClubSeries {
  colour: number;
  shape: SeriesShape;
}

/**
 * Assigned in first-shot order so a new club never repaints the others. The
 * shape varies too, so neighbours differ by more than hue and no pair repeats.
 */
export function clubSeries(shots: readonly Shot[]): Map<string, ClubSeries> {
  const series = new Map<string, ClubSeries>();
  for (const shot of ballShots(shots)) {
    if (series.has(shot.club)) continue;
    const index = series.size;
    series.set(shot.club, {
      colour: (index % SERIES_COLOURS) + 1,
      shape: SERIES_SHAPES[(index + Math.floor(index / SERIES_COLOURS)) % SERIES_SHAPES.length],
    });
  }
  return series;
}

export interface DispersionGroup {
  club: string;
  points: Point2D[];
  ellipse: CovarianceEllipse | null;
  insideFraction: number | null;
}

export function buildDispersion(shots: readonly Shot[]): DispersionGroup[] {
  const byClub = new Map<string, Point2D[]>();
  for (const shot of ballShots(shots)) {
    if (!hasFlight(shot)) continue;
    const points = byClub.get(shot.club) ?? [];
    points.push({ x: shot.flight.carry_yards, y: shot.flight.lateral_yards });
    byClub.set(shot.club, points);
  }
  return [...byClub].map(([club, points]) => {
    const ellipse = covarianceEllipse(points);
    return { club, points, ellipse, insideFraction: ellipse ? fractionInsideEllipse(points, ellipse) : null };
  });
}

export const GAPPING_WINDOW = 10;
export const MISHIT_SD = 2;

/**
 * Mishit: a carry more than MISHIT_SD sample SDs from its club's mean over the
 * window, dropped in one pass (a single carry can only get that far out of 6+).
 */
export function excludeMishits(carries: readonly number[]): { kept: number[]; excluded: number } {
  const average = mean(carries);
  const stdDev = sampleStdDev(carries);
  if (average === null || stdDev === null || stdDev === 0) return { kept: [...carries], excluded: 0 };
  const kept = carries.filter((carry) => Math.abs(carry - average) <= MISHIT_SD * stdDev);
  return { kept, excluded: carries.length - kept.length };
}

export interface GappingRow {
  club: string;
  shots: number;
  excluded: number;
  average: number;
  stdDev: number | null;
  min: number;
  max: number;
  gapToNext: number | null;
}

export interface GappingOptions {
  window?: number;
  excludeMishits?: boolean;
}

export function buildGapping(shots: readonly Shot[], options: GappingOptions = {}): GappingRow[] {
  const window = options.window ?? GAPPING_WINDOW;
  const byClub = new Map<string, number[]>();
  for (const shot of ballShots(shots)) {
    const carries = byClub.get(shot.club) ?? [];
    carries.push(carryYards(shot));
    byClub.set(shot.club, carries);
  }

  const rows: GappingRow[] = [];
  for (const [club, carries] of byClub) {
    const recent = carries.slice(-window);
    const { kept, excluded } = options.excludeMishits ? excludeMishits(recent) : { kept: recent, excluded: 0 };
    rows.push({
      club,
      shots: kept.length,
      excluded,
      average: mean(kept) ?? 0,
      stdDev: sampleStdDev(kept),
      min: Math.min(...kept),
      max: Math.max(...kept),
      gapToNext: null,
    });
  }

  rows.sort((a, b) => b.average - a.average || a.club.localeCompare(b.club));
  for (let index = 0; index < rows.length - 1; index += 1) {
    rows[index].gapToNext = rows[index].average - rows[index + 1].average;
  }
  return rows;
}
