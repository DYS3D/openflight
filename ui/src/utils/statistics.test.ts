import { describe, expect, it } from 'vitest';
import {
  covarianceEllipse,
  ellipseOutline,
  fractionInsideEllipse,
  isInsideEllipse,
  mean,
  niceCeil,
  niceTicks,
  sampleStdDev,
  symmetricTicks,
} from './statistics';

describe('mean and sampleStdDev', () => {
  it('uses the n - 1 sample standard deviation', () => {
    expect(mean([2, 4, 4, 4, 5, 5, 7, 9])).toBe(5);
    expect(sampleStdDev([2, 4, 4, 4, 5, 5, 7, 9])).toBeCloseTo(2.13809, 5);
  });

  it('has no mean for an empty list and no spread below two values', () => {
    expect(mean([])).toBeNull();
    expect(sampleStdDev([150])).toBeNull();
    expect(sampleStdDev([150, 150])).toBe(0);
  });
});

describe('covarianceEllipse', () => {
  it('needs at least three points', () => {
    expect(
      covarianceEllipse([
        { x: 0, y: 0 },
        { x: 1, y: 1 },
      ])
    ).toBeNull();
  });

  it('aligns with the axes for uncorrelated spread', () => {
    const points = [
      { x: 148, y: 5 },
      { x: 152, y: 5 },
      { x: 150, y: 4 },
      { x: 150, y: 6 },
      { x: 148, y: 5 },
      { x: 152, y: 5 },
    ];
    const ellipse = covarianceEllipse(points)!;

    expect(ellipse.cx).toBe(150);
    expect(ellipse.cy).toBe(5);
    expect(ellipse.rx).toBeCloseTo(Math.sqrt(16 / 5), 6);
    expect(ellipse.ry).toBeCloseTo(Math.sqrt(2 / 5), 6);
    expect(Math.abs(ellipse.angle)).toBeCloseTo(0, 6);
  });

  it('rotates the major axis along correlated points', () => {
    const ellipse = covarianceEllipse([
      { x: 0, y: 0 },
      { x: 1, y: 1.1 },
      { x: 2, y: 1.9 },
      { x: 3, y: 3 },
    ])!;

    expect(ellipse.angle).toBeCloseTo(Math.PI / 4, 1);
    expect(ellipse.rx).toBeGreaterThan(ellipse.ry * 10);
  });

  it('collapses to a line when every point is collinear', () => {
    const points = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 2, y: 0 },
    ];
    const ellipse = covarianceEllipse(points)!;

    expect(ellipse.ry).toBe(0);
    expect(isInsideEllipse({ x: 1.5, y: 0 }, ellipse)).toBe(true);
    expect(isInsideEllipse({ x: 1, y: 0.01 }, ellipse)).toBe(false);
  });
});

describe('fractionInsideEllipse', () => {
  it('counts points within one standard deviation', () => {
    const points = [
      { x: -2, y: 0 },
      { x: 2, y: 0 },
      { x: 0, y: -1 },
      { x: 0, y: 1 },
      { x: 0, y: 0 },
    ];
    const ellipse = covarianceEllipse(points)!;

    expect(fractionInsideEllipse(points, ellipse)).toBeCloseTo(1 / 5, 6);
    expect(fractionInsideEllipse([], ellipse)).toBe(0);
  });

  it('draws an outline whose vertices sit on the ellipse', () => {
    const ellipse = { cx: 10, cy: -3, rx: 4, ry: 2, angle: 0.6 };
    for (const vertex of ellipseOutline(ellipse, 12)) {
      expect(isInsideEllipse(vertex, ellipse)).toBe(true);
      expect(isInsideEllipse({ x: 10 + (vertex.x - 10) * 1.01, y: -3 + (vertex.y + 3) * 1.01 }, ellipse)).toBe(false);
    }
  });
});

describe('axis helpers', () => {
  it('picks 1/2/5 steps', () => {
    expect(niceTicks(0, 250, 5)).toEqual([0, 50, 100, 150, 200, 250]);
    expect(niceTicks(-20, 20, 4)).toEqual([-20, -10, 0, 10, 20]);
    expect(niceTicks(0, 32, 3)).toEqual([0, 20]);
  });

  it('labels both sides of a zero-centred axis', () => {
    expect(symmetricTicks(30, 5)).toEqual([-30, -15, 0, 15, 30]);
    expect(symmetricTicks(15, 5)).toEqual([-15, 0, 15]);
    expect(symmetricTicks(30, 2)).toEqual([-30, 0, 30]);
  });

  it('rounds an axis limit up to a round number', () => {
    expect(niceCeil(212)).toBe(250);
    expect(niceCeil(31)).toBe(40);
    expect(niceCeil(0)).toBe(1);
  });
});
