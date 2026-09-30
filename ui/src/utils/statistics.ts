export interface Point2D {
  x: number;
  y: number;
}

export interface CovarianceEllipse {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  angle: number;
}

export function mean(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function sampleStdDev(values: readonly number[]): number | null {
  if (values.length < 2) return null;
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  const squares = values.reduce((sum, value) => sum + (value - average) ** 2, 0);
  return Math.sqrt(squares / (values.length - 1));
}

export const MIN_ELLIPSE_POINTS = 3;

export function covarianceEllipse(points: readonly Point2D[]): CovarianceEllipse | null {
  if (points.length < MIN_ELLIPSE_POINTS) return null;
  const n = points.length;
  const cx = points.reduce((sum, point) => sum + point.x, 0) / n;
  const cy = points.reduce((sum, point) => sum + point.y, 0) / n;
  let sxx = 0;
  let syy = 0;
  let sxy = 0;
  for (const point of points) {
    const dx = point.x - cx;
    const dy = point.y - cy;
    sxx += dx * dx;
    syy += dy * dy;
    sxy += dx * dy;
  }
  sxx /= n - 1;
  syy /= n - 1;
  sxy /= n - 1;

  const halfTrace = (sxx + syy) / 2;
  const root = Math.sqrt(((sxx - syy) / 2) ** 2 + sxy ** 2);
  const major = halfTrace + root;
  const minor = Math.max(0, halfTrace - root);
  const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);

  return { cx, cy, rx: Math.sqrt(major), ry: Math.sqrt(minor), angle };
}

const INSIDE_EPSILON = 1e-9;

export function isInsideEllipse(point: Point2D, ellipse: CovarianceEllipse): boolean {
  const dx = point.x - ellipse.cx;
  const dy = point.y - ellipse.cy;
  const cos = Math.cos(ellipse.angle);
  const sin = Math.sin(ellipse.angle);
  const along = dx * cos + dy * sin;
  const across = -dx * sin + dy * cos;
  const term = (offset: number, radius: number) => {
    if (radius > INSIDE_EPSILON) return (offset / radius) ** 2;
    return Math.abs(offset) <= INSIDE_EPSILON ? 0 : Infinity;
  };
  return term(along, ellipse.rx) + term(across, ellipse.ry) <= 1 + INSIDE_EPSILON;
}

export function fractionInsideEllipse(points: readonly Point2D[], ellipse: CovarianceEllipse): number {
  if (points.length === 0) return 0;
  return points.filter((point) => isInsideEllipse(point, ellipse)).length / points.length;
}

export function ellipseOutline(ellipse: CovarianceEllipse, segments = 48): Point2D[] {
  const cos = Math.cos(ellipse.angle);
  const sin = Math.sin(ellipse.angle);
  const outline: Point2D[] = [];
  for (let index = 0; index < segments; index += 1) {
    const theta = (index / segments) * 2 * Math.PI;
    const u = ellipse.rx * Math.cos(theta);
    const v = ellipse.ry * Math.sin(theta);
    outline.push({ x: ellipse.cx + u * cos - v * sin, y: ellipse.cy + u * sin + v * cos });
  }
  return outline;
}

export function niceTicks(min: number, max: number, targetCount: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [min];
  const rawStep = (max - min) / Math.max(1, targetCount);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude;
  const ticks: number[] = [];
  for (let tick = Math.ceil(min / step) * step; tick <= max + step * 1e-9; tick += step) {
    ticks.push(Number(tick.toFixed(10)));
  }
  return ticks;
}

export function niceCeil(value: number): number {
  if (!(value > 0)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (value <= step * magnitude + 1e-9) return step * magnitude;
  }
  return 10 * magnitude;
}

export function symmetricTicks(limit: number, targetCount: number): number[] {
  const half = limit / 2;
  if (targetCount >= 4 && Number.isInteger(half)) return [-limit, -half, 0, half, limit];
  return [-limit, 0, limit];
}
