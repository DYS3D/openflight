import { convertDistanceFromYards, convertDistanceToYards, type UnitSystem } from './units';

export type PracticeMode = 'target' | 'ladder';

export const ROUND_SHOTS = 10;
export const MAX_POINTS = 5;
export const LADDER_STEP = 10;
/** A hit (carry within 10% of the target) moves the ladder up. */
export const HIT_POINTS = 3;
export const TARGET_STEP = 10;
export const TARGET_MIN = 10;
export const TARGET_MAX = 300;

const SCORE_BANDS: ReadonlyArray<readonly [number, number]> = [
  [3, 5],
  [6, 4],
  [10, 3],
  [15, 2],
  [20, 1],
];

export function carryErrorPercent(carryYards: number, targetYards: number): number {
  return (Math.abs(carryYards - targetYards) / targetYards) * 100;
}

export function scoreShot(carryYards: number, targetYards: number): number {
  const miss = Math.abs(carryYards - targetYards);
  for (const [percent, points] of SCORE_BANDS) {
    if (miss <= (percent / 100) * targetYards + 1e-9) return points;
  }
  return 0;
}

export interface PracticeConfig {
  mode: PracticeMode;
  /** Targets are whole numbers in this unit. */
  unitSystem: UnitSystem;
  minTarget: number;
  maxTarget: number;
  ladderStart: number;
  seed: number;
}

export function defaultPracticeConfig(unitSystem: UnitSystem, seed: number): PracticeConfig {
  return { mode: 'target', unitSystem, minTarget: 50, maxTarget: 150, ladderStart: 50, seed };
}

/** mulberry32, so a round's targets can be recomputed from its seed. */
export function seededUnit(seed: number, index: number): number {
  let state = (seed ^ Math.imul(index + 1, 0x9e3779b1)) + 0x6d2b79f5;
  state = Math.imul(state ^ (state >>> 15), state | 1);
  state ^= state + Math.imul(state ^ (state >>> 7), state | 61);
  return ((state ^ (state >>> 14)) >>> 0) / 4294967296;
}

export function randomTarget(seed: number, index: number, min: number, max: number): number {
  return min + Math.floor(seededUnit(seed, index) * (max - min + 1));
}

function targetFor(config: PracticeConfig, index: number, hits: number): number {
  return config.mode === 'ladder'
    ? config.ladderStart + LADDER_STEP * hits
    : randomTarget(config.seed, index, config.minTarget, config.maxTarget);
}

export interface PracticeAttempt {
  target: number;
  targetYards: number;
  carryYards: number;
  errorPercent: number;
  points: number;
  hit: boolean;
}

export interface PracticeRound {
  attempts: PracticeAttempt[];
  nextTarget: number | null;
  totalPoints: number;
  averagePoints: number | null;
  hits: number;
  complete: boolean;
}

export function playRound(config: PracticeConfig, carriesYards: readonly number[]): PracticeRound {
  const attempts: PracticeAttempt[] = [];
  let hits = 0;
  for (const [index, carry] of carriesYards.slice(0, ROUND_SHOTS).entries()) {
    const target = targetFor(config, index, hits);
    const targetYards = convertDistanceToYards(target, config.unitSystem);
    const points = scoreShot(carry, targetYards);
    const hit = points >= HIT_POINTS;
    if (hit) hits += 1;
    attempts.push({
      target,
      targetYards,
      carryYards: carry,
      errorPercent: carryErrorPercent(carry, targetYards),
      points,
      hit,
    });
  }
  const totalPoints = attempts.reduce((sum, attempt) => sum + attempt.points, 0);
  const complete = attempts.length >= ROUND_SHOTS;
  return {
    attempts,
    nextTarget: complete ? null : targetFor(config, attempts.length, hits),
    totalPoints,
    averagePoints: attempts.length > 0 ? totalPoints / attempts.length : null,
    hits,
    complete,
  };
}

export type PracticeSetting = 'minTarget' | 'maxTarget' | 'ladderStart';

export function stepSetting(config: PracticeConfig, setting: PracticeSetting, direction: 1 | -1): PracticeConfig {
  const value = config[setting] + direction * TARGET_STEP;
  const low = setting === 'maxTarget' ? config.minTarget + TARGET_STEP : TARGET_MIN;
  const high = setting === 'minTarget' ? config.maxTarget - TARGET_STEP : TARGET_MAX;
  return { ...config, [setting]: Math.min(high, Math.max(low, value)) };
}

function roundToStep(value: number): number {
  return Math.max(TARGET_MIN, Math.min(TARGET_MAX, Math.round(value / TARGET_STEP) * TARGET_STEP));
}

export function convertPracticeConfig(config: PracticeConfig, unitSystem: UnitSystem): PracticeConfig {
  const convert = (value: number) =>
    roundToStep(convertDistanceFromYards(convertDistanceToYards(value, config.unitSystem), unitSystem));
  const minTarget = Math.min(TARGET_MAX - TARGET_STEP, convert(config.minTarget));
  return {
    ...config,
    unitSystem,
    minTarget,
    maxTarget: Math.max(minTarget + TARGET_STEP, convert(config.maxTarget)),
    ladderStart: convert(config.ladderStart),
  };
}
