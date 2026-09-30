import { useRef } from 'react';
import type { ClubSeries, DispersionGroup } from '../../utils/shotAnalysis';
import { ellipseOutline, niceCeil, niceTicks, symmetricTicks, type Point2D } from '../../utils/statistics';
import { convertDistanceFromYards, type UnitSystem } from '../../utils/units';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useI18n } from '../../i18n/useI18n';
import { SeriesMarker } from './SeriesMarker';
import { useChartSize } from './useChartSize';
import './charts.css';

const FALLBACK_SERIES: ClubSeries = { colour: 1, shape: 'circle' };

interface DispersionChartProps {
  groups: readonly DispersionGroup[];
  series: ReadonlyMap<string, ClubSeries>;
  highlightedClub: string | null;
  unitSystem: UnitSystem;
  caption: string;
  ariaLabel: string;
}

/** Carry runs left to right, so a landing right of target sits below the line. */
export function DispersionChart({
  groups,
  series,
  highlightedClub,
  unitSystem,
  caption,
  ariaLabel,
}: DispersionChartProps) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const { width, height, fontPx } = useChartSize(ref);
  const toUnit = (yards: number) => convertDistanceFromYards(yards, unitSystem);

  const outlines = new Map(groups.map((group) => [group.club, group.ellipse ? ellipseOutline(group.ellipse) : []]));
  const everything: Point2D[] = groups.flatMap((group) => [...group.points, ...(outlines.get(group.club) ?? [])]);
  const carries = everything.map((point) => toUnit(point.x));
  const low = Math.min(...carries);
  const high = Math.max(...carries);
  const pad = Math.max((high - low) * 0.08, 5);
  const xMin = Math.max(0, low - pad);
  const xMax = high + pad;
  const yLimit = niceCeil(Math.max(...everything.map((point) => Math.abs(toUnit(point.y))), 5) * 1.1);

  const margin = { left: fontPx * 3.4, right: fontPx * 1.2, top: fontPx * 1.8, bottom: fontPx * 1.9 };
  const plotWidth = Math.max(1, width - margin.left - margin.right);
  const plotHeight = Math.max(1, height - margin.top - margin.bottom);
  const x = (value: number) => margin.left + ((value - xMin) / (xMax - xMin)) * plotWidth;
  const y = (value: number) => margin.top + plotHeight / 2 + (value / yLimit) * (plotHeight / 2);

  const xTicks = niceTicks(xMin, xMax, Math.min(8, Math.max(2, Math.round(plotWidth / (fontPx * 5)))));
  const yTicks = symmetricTicks(yLimit, Math.round(plotHeight / (fontPx * 2.4)));
  const markerSize = Math.max(4, fontPx * 0.42);
  const ordered = highlightedClub
    ? [
        ...groups.filter((group) => group.club !== highlightedClub),
        ...groups.filter((group) => group.club === highlightedClub),
      ]
    : groups;
  const tickLabel = (value: number) =>
    value === 0 ? '0' : `${Math.abs(value)} ${value < 0 ? t('chart.left') : t('chart.right')}`;

  return (
    <div className="chart dispersion-chart" ref={ref}>
      <svg className="chart__svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        <text className="chart__caption" x={margin.left} y={fontPx * 1.1}>
          {caption}
        </text>
        {yTicks.map((value) => (
          <g key={value}>
            <line
              className={value === 0 ? 'chart__target' : 'chart__grid'}
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
              {tickLabel(value)}
            </text>
          </g>
        ))}
        {xTicks.map((value) => (
          <text key={value} className="chart__tick" x={x(value)} y={height - fontPx * 0.4} textAnchor="middle">
            {value}
          </text>
        ))}
        {ordered.map((group) => {
          const style = series.get(group.club) ?? FALLBACK_SERIES;
          const dimmed = highlightedClub !== null && highlightedClub !== group.club;
          const outline = outlines.get(group.club) ?? [];
          return (
            <g
              key={group.club}
              className={`chart-series-${style.colour}${dimmed ? ' dispersion-chart__group--dimmed' : ''}`}
              data-club={group.club}
            >
              {outline.length > 0 ? (
                <path
                  className="dispersion-chart__ellipse"
                  d={`${outline
                    .map(
                      (point, index) =>
                        `${index === 0 ? 'M' : 'L'}${x(toUnit(point.x)).toFixed(1)},${y(toUnit(point.y)).toFixed(1)}`
                    )
                    .join('')}Z`}
                />
              ) : null}
              {group.points.map((point, index) => (
                <SeriesMarker
                  key={index}
                  className="dispersion-chart__marker"
                  shape={style.shape}
                  cx={x(toUnit(point.x))}
                  cy={y(toUnit(point.y))}
                  size={markerSize}
                />
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

interface DispersionLegendProps {
  groups: readonly DispersionGroup[];
  series: ReadonlyMap<string, ClubSeries>;
  highlightedClub: string | null;
  onToggleClub?: (club: string) => void;
}

export function DispersionLegend({ groups, series, highlightedClub, onToggleClub }: DispersionLegendProps) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(ref, 'x');

  return (
    <div
      className="chart-legend"
      role="group"
      aria-label={t('dispersion.legendAria')}
      ref={ref}
      onPointerDown={dragScroll.onPointerDown}
      onPointerMove={dragScroll.onPointerMove}
      onPointerUp={dragScroll.onPointerUp}
      onPointerCancel={dragScroll.onPointerCancel}
      onClickCapture={dragScroll.onClickCapture}
    >
      {groups.map((group) => {
        const style = series.get(group.club) ?? FALLBACK_SERIES;
        const active = highlightedClub === group.club;
        const className = [
          'chart-legend__item',
          active ? 'chart-legend__item--active' : '',
          highlightedClub !== null && !active ? 'chart-legend__item--dimmed' : '',
        ]
          .filter(Boolean)
          .join(' ');
        const content = (
          <>
            <svg className={`chart-legend__swatch chart-series-${style.colour}`} viewBox="0 0 14 14" aria-hidden="true">
              <SeriesMarker className="dispersion-chart__marker" shape={style.shape} cx={7} cy={7} size={5} />
            </svg>
            <span className="chart-legend__club">{group.club}</span>
            <span className="chart-legend__detail">
              {group.insideFraction !== null
                ? t('dispersion.inside', { percent: Math.round(group.insideFraction * 100) })
                : t(group.points.length === 1 ? 'profiles.shot' : 'profiles.shots', { count: group.points.length })}
            </span>
          </>
        );
        return onToggleClub ? (
          <button
            key={group.club}
            type="button"
            className={className}
            aria-pressed={active}
            onClick={() => onToggleClub(group.club)}
          >
            {content}
          </button>
        ) : (
          <span key={group.club} className={className}>
            {content}
          </span>
        );
      })}
    </div>
  );
}
