import { useRef, type ReactNode } from 'react';
import type { ShotFlight } from '../../types/shot';
import type { FlightExtent } from '../../utils/shotAnalysis';
import { niceCeil, niceTicks, symmetricTicks } from '../../utils/statistics';
import { convertDistanceFromYards, type UnitSystem } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { useChartSize } from './useChartSize';
import './charts.css';

export type FlightView = 'side' | 'top';

interface FlightChartProps {
  view: FlightView;
  latest: ShotFlight;
  ghosts: readonly ShotFlight[];
  extent: FlightExtent;
  unitSystem: UnitSystem;
  caption: string;
  ariaLabel: string;
  legend?: ReactNode;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** In the top view the golfer stands at the left edge, so a ball finishing right sits below the line. */
export function FlightChart({
  view,
  latest,
  ghosts,
  extent,
  unitSystem,
  caption,
  ariaLabel,
  legend,
}: FlightChartProps) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const { width, height, fontPx } = useChartSize(ref);
  const toUnit = (yards: number) => convertDistanceFromYards(yards, unitSystem);

  const margin = { left: fontPx * 3.4, right: fontPx * 1.2, top: fontPx * 1.8, bottom: fontPx * 1.9 };
  const plotWidth = Math.max(1, width - margin.left - margin.right);
  const plotHeight = Math.max(1, height - margin.top - margin.bottom);
  const xMax = niceCeil(toUnit(extent.downrange) * 1.03);
  const x = (value: number) => margin.left + (value / xMax) * plotWidth;

  const yLimit =
    view === 'side'
      ? niceCeil(toUnit(extent.height) * 1.1)
      : niceCeil(Math.max(toUnit(extent.lateral) * 1.15, xMax * 0.04));
  const y =
    view === 'side'
      ? (value: number) => margin.top + plotHeight - (value / yLimit) * plotHeight
      : (value: number) => margin.top + plotHeight / 2 + (value / yLimit) * (plotHeight / 2);

  const xTicks = niceTicks(0, xMax, clamp(Math.round(plotWidth / (fontPx * 5)), 2, 8));
  const yTickCount = clamp(Math.round(plotHeight / (fontPx * 2.4)), 1, 4);
  const yTicks = view === 'side' ? niceTicks(0, yLimit, yTickCount) : symmetricTicks(yLimit, yTickCount);

  const vertical = (point: readonly [number, number, number]) => (view === 'side' ? point[2] : point[1]);
  const path = (flight: ShotFlight) =>
    flight.points
      .map(
        (point, index) =>
          `${index === 0 ? 'M' : 'L'}${x(toUnit(point[0])).toFixed(1)},${y(toUnit(vertical(point))).toFixed(1)}`
      )
      .join('');
  const landing = latest.points[latest.points.length - 1];
  const yTickLabel = (value: number) => {
    if (view === 'side' || value === 0) return String(Math.abs(value));
    return `${Math.abs(value)} ${value < 0 ? t('chart.left') : t('chart.right')}`;
  };

  return (
    <div className="chart flight-chart" ref={ref}>
      <svg className="chart__svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        <text className="chart__caption" x={margin.left} y={fontPx * 1.1}>
          {caption}
        </text>
        {yTicks.map((value) => (
          <g key={value}>
            <line
              className={value === 0 ? (view === 'side' ? 'chart__baseline' : 'chart__target') : 'chart__grid'}
              x1={margin.left}
              x2={margin.left + plotWidth}
              y1={y(value)}
              y2={y(value)}
            />
            <text
              className="chart__tick"
              x={margin.left - fontPx * 0.5}
              y={y(value)}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {yTickLabel(value)}
            </text>
          </g>
        ))}
        {xTicks.map((value) => (
          <text key={value} className="chart__tick" x={x(value)} y={height - fontPx * 0.4} textAnchor="middle">
            {value}
          </text>
        ))}
        {ghosts.map((flight, index) => (
          <path key={index} className="flight-chart__ghost" d={path(flight)} />
        ))}
        <path className="flight-chart__latest" d={path(latest)} />
        <circle
          className="flight-chart__landing"
          cx={x(toUnit(landing[0]))}
          cy={y(toUnit(vertical(landing)))}
          r={Math.max(4, fontPx * 0.36)}
        />
      </svg>
      {legend ? <div className="chart__legend">{legend}</div> : null}
    </div>
  );
}

export function FlightKey({ ghostCount }: { ghostCount: number }) {
  const { t } = useI18n();
  return (
    <>
      <span className="flight-key flight-key--latest">{t('flight.latest')}</span>
      {ghostCount > 0 ? (
        <span className="flight-key flight-key--ghost">{t('flight.previous', { count: ghostCount })}</span>
      ) : null}
    </>
  );
}
