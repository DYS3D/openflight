import { describe, expect, it } from 'vitest';
import {
  carryErrorPercent,
  convertPracticeConfig,
  defaultPracticeConfig,
  playRound,
  randomTarget,
  ROUND_SHOTS,
  scoreShot,
  stepSetting,
  type PracticeConfig,
} from './practice';

describe('scoreShot', () => {
  it.each([
    [100, 5],
    [103, 5],
    [97, 5],
    [103.5, 4],
    [106, 4],
    [110, 3],
    [89.9, 2],
    [115, 2],
    [120, 1],
    [79.9, 0],
    [150, 0],
  ])('scores a %d yd carry at a 100 yd target as %d', (carry, points) => {
    expect(scoreShot(carry, 100)).toBe(points);
  });

  it('measures error relative to the target, not in yards', () => {
    expect(scoreShot(156, 150)).toBe(4);
    expect(scoreShot(56, 50)).toBe(2);
    expect(carryErrorPercent(56, 50)).toBeCloseTo(12, 6);
  });
});

describe('randomTarget', () => {
  it('stays inside the chosen range and repeats for the same seed', () => {
    for (let index = 0; index < 200; index += 1) {
      const target = randomTarget(42, index, 50, 150);
      expect(Number.isInteger(target)).toBe(true);
      expect(target).toBeGreaterThanOrEqual(50);
      expect(target).toBeLessThanOrEqual(150);
      expect(randomTarget(42, index, 50, 150)).toBe(target);
    }
  });

  it('varies with the seed and from shot to shot', () => {
    const first = Array.from({ length: 10 }, (_, index) => randomTarget(1, index, 50, 150));
    const second = Array.from({ length: 10 }, (_, index) => randomTarget(2, index, 50, 150));

    expect(new Set(first).size).toBeGreaterThan(5);
    expect(first).not.toEqual(second);
  });
});

describe('playRound', () => {
  const target: PracticeConfig = defaultPracticeConfig('imperial', 7);
  const ladder: PracticeConfig = { ...target, mode: 'ladder', ladderStart: 60 };

  it('starts with a target and no score', () => {
    const round = playRound(target, []);

    expect(round.attempts).toEqual([]);
    expect(round.nextTarget).toBe(randomTarget(7, 0, 50, 150));
    expect(round.totalPoints).toBe(0);
    expect(round.averagePoints).toBeNull();
    expect(round.complete).toBe(false);
  });

  it('scores each shot against the target it was shown', () => {
    const firstTarget = randomTarget(7, 0, 50, 150);
    const secondTarget = randomTarget(7, 1, 50, 150);

    const round = playRound(target, [firstTarget, secondTarget * 1.5]);

    expect(round.attempts.map((attempt) => [attempt.target, attempt.points])).toEqual([
      [firstTarget, 5],
      [secondTarget, 0],
    ]);
    expect(round.totalPoints).toBe(5);
    expect(round.averagePoints).toBe(2.5);
    expect(round.nextTarget).toBe(randomTarget(7, 2, 50, 150));
  });

  it('finishes after ten shots and ignores extra ones', () => {
    const carries = Array.from({ length: ROUND_SHOTS + 3 }, (_, index) => randomTarget(7, index, 50, 150));

    const round = playRound(target, carries);

    expect(round.attempts).toHaveLength(ROUND_SHOTS);
    expect(round.complete).toBe(true);
    expect(round.nextTarget).toBeNull();
    expect(round.totalPoints).toBe(50);
  });

  it('climbs the ladder 10 yards only after a hit', () => {
    const round = playRound(ladder, [60, 40, 64]);

    expect(round.attempts.map((attempt) => attempt.target)).toEqual([60, 70, 70]);
    expect(round.attempts.map((attempt) => attempt.hit)).toEqual([true, false, true]);
    expect(round.hits).toBe(2);
    expect(round.nextTarget).toBe(80);
  });

  it('keeps metric targets in metres and scores in yards', () => {
    const metric = { ...ladder, unitSystem: 'metric' as const, ladderStart: 100 };

    const round = playRound(metric, [100 / 0.9144]);

    expect(round.attempts[0].target).toBe(100);
    expect(round.attempts[0].targetYards).toBeCloseTo(109.36, 2);
    expect(round.attempts[0].points).toBe(5);
    expect(round.nextTarget).toBe(110);
  });
});

describe('practice settings', () => {
  const config = defaultPracticeConfig('imperial', 1);

  it('defaults to a 50–150 target range', () => {
    expect(config).toMatchObject({ mode: 'target', minTarget: 50, maxTarget: 150 });
  });

  it('steps by 10 without crossing the other end of the range', () => {
    expect(stepSetting(config, 'minTarget', 1).minTarget).toBe(60);
    expect(stepSetting({ ...config, minTarget: 140 }, 'minTarget', 1).minTarget).toBe(140);
    expect(stepSetting({ ...config, maxTarget: 60 }, 'maxTarget', -1).maxTarget).toBe(60);
    expect(stepSetting({ ...config, minTarget: 10 }, 'minTarget', -1).minTarget).toBe(10);
    expect(stepSetting({ ...config, maxTarget: 300 }, 'maxTarget', 1).maxTarget).toBe(300);
    expect(stepSetting(config, 'ladderStart', 1).ladderStart).toBe(60);
  });

  it('converts the range to metres on the same 10-unit grid', () => {
    expect(convertPracticeConfig(config, 'metric')).toMatchObject({
      unitSystem: 'metric',
      minTarget: 50,
      maxTarget: 140,
      ladderStart: 50,
    });
    expect(
      convertPracticeConfig({ ...config, unitSystem: 'metric', minTarget: 290, maxTarget: 300 }, 'imperial')
    ).toMatchObject({
      minTarget: 290,
      maxTarget: 300,
    });
  });
});
