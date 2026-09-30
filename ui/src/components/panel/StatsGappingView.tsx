import { useMemo, useRef, useState } from 'react';
import type { Shot } from '../../types/shot';
import { buildGapping, clubSeries, GAPPING_WINDOW, type ClubSeries } from '../../utils/shotAnalysis';
import { niceCeil } from '../../utils/statistics';
import { convertDistanceFromYards, formatDistance, getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useI18n } from '../../i18n/useI18n';
import { SeriesMarker } from '../charts/SeriesMarker';
import '../charts/charts.css';

const FALLBACK_SERIES: ClubSeries = { colour: 1, shape: 'circle' };

interface StatsGappingViewProps {
  shots: Shot[];
  unitSystem: UnitSystem;
  /** For tests: renderToString cannot tap. */
  initialExcludeMishits?: boolean;
}

export function StatsGappingView({ shots, unitSystem, initialExcludeMishits = false }: StatsGappingViewProps) {
  const { t } = useI18n();
  const [excludeMishits, setExcludeMishits] = useState(initialExcludeMishits);
  const rows = useMemo(() => buildGapping(shots, { excludeMishits }), [shots, excludeMishits]);
  const series = useMemo(() => clubSeries(shots), [shots]);
  const listRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(listRef);

  if (rows.length === 0) {
    return (
      <div className="panel__body--empty">
        <span className="panel__empty-title">{t('stats.noShots')}</span>
        <span className="panel__empty-detail">{t('gapping.noDataDetail')}</span>
      </div>
    );
  }

  const fmt = (yards: number) => formatDistance(yards, unitSystem, 0);
  const scaleMax = niceCeil(convertDistanceFromYards(Math.max(...rows.map((row) => row.max)), unitSystem));
  const percent = (yards: number) => (convertDistanceFromYards(yards, unitSystem) / scaleMax) * 100;
  const excluded = rows.reduce((sum, row) => sum + row.excluded, 0);

  return (
    <div className="stats-gapping">
      <div className="stats-gapping__bar">
        <span className="stats-gapping__caption">
          <span>{t('gapping.window', { count: GAPPING_WINDOW })}</span>
          <span className="stats-gapping__rule">
            {excludeMishits ? `${t('gapping.excluded', { count: excluded })} · ` : ''}
            {t('gapping.rule')}
          </span>
        </span>
        <button
          type="button"
          className={`panel-chip${excludeMishits ? ' panel-chip--active' : ''}`}
          aria-pressed={excludeMishits}
          onClick={() => setExcludeMishits((value) => !value)}
        >
          {t('gapping.excludeMishits')}
        </button>
      </div>
      <div className="stats-gapping__columns" role="presentation">
        <span>{t('shots.colClub')}</span>
        <span>{t('gapping.colCarry', { unit: getDistanceUnit(unitSystem) })}</span>
        <span className="stats-gapping__num">{t('gapping.colAverage')}</span>
        <span className="stats-gapping__num">{t('gapping.colRange')}</span>
        <span className="stats-gapping__num">{t('gapping.colGap')}</span>
      </div>
      <div
        className="stats-gapping__rows"
        role="list"
        aria-label={t('stats.viewGapping')}
        ref={listRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        {rows.map((row) => {
          const style = series.get(row.club) ?? FALLBACK_SERIES;
          const sdLow = row.stdDev === null ? null : percent(row.average - row.stdDev);
          const sdHigh = row.stdDev === null ? null : percent(row.average + row.stdDev);
          return (
            <div key={row.club} className="stats-gapping__row" role="listitem">
              <span className="stats-gapping__club">
                <svg
                  className={`chart-legend__swatch chart-series-${style.colour}`}
                  viewBox="0 0 14 14"
                  aria-hidden="true"
                >
                  <SeriesMarker className="dispersion-chart__marker" shape={style.shape} cx={7} cy={7} size={5} />
                </svg>
                <span className="stats-gapping__club-name">{row.club}</span>
                <span className="stats-gapping__count">{row.shots}</span>
              </span>
              <svg
                className={`stats-gapping__track chart-series-${style.colour}`}
                viewBox="0 0 100 10"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <rect className="stats-gapping__bar-fill" x={0} y={2.5} width={percent(row.average)} height={5} />
                <line className="stats-gapping__range" x1={percent(row.min)} x2={percent(row.max)} y1={5} y2={5} />
                {sdLow !== null && sdHigh !== null ? (
                  <g className="stats-gapping__sd">
                    <line x1={sdLow} x2={sdHigh} y1={5} y2={5} />
                    <line x1={sdLow} x2={sdLow} y1={1} y2={9} />
                    <line x1={sdHigh} x2={sdHigh} y1={1} y2={9} />
                  </g>
                ) : null}
              </svg>
              <span className="stats-gapping__num stats-gapping__value">
                {fmt(row.average)}
                <span className="stats-gapping__sd-text"> ± {row.stdDev === null ? '—' : fmt(row.stdDev)}</span>
              </span>
              <span className="stats-gapping__num stats-gapping__muted">
                {fmt(row.min)}–{fmt(row.max)}
              </span>
              <span className="stats-gapping__num stats-gapping__value">
                {row.gapToNext === null ? '—' : fmt(row.gapToNext)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
