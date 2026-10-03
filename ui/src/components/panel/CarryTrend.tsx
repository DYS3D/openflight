import type { Shot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { formatDistance, getDistanceUnit } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { liveCarryYards } from './liveMetrics';

export const CARRY_TREND_COUNT = 6;

interface CarryTrendProps {
  /** The golfer's shots, oldest first; the last one is the shot on screen. */
  shots: Shot[];
  unitSystem: UnitSystem;
}

/** Copper Live: carry for the last few shots, the newest bar in copper. */
export function CarryTrend({ shots, unitSystem }: CarryTrendProps) {
  const { t } = useI18n();
  const recent = shots.slice(-CARRY_TREND_COUNT);
  if (recent.length < 2) return null;

  const carries = recent.map(liveCarryYards);
  const min = Math.min(...carries);
  const max = Math.max(...carries);
  // Stretch the spread so a 5-yard miss is visible; the shortest bar is 30%.
  const height = (carry: number) => (max > min ? 30 + (70 * (carry - min)) / (max - min) : 100);
  const unit = getDistanceUnit(unitSystem);
  const summary = carries
    .map((carry, index) =>
      t('live.carryTrendShot', { index: index + 1, value: formatDistance(carry, unitSystem, 0), unit })
    )
    .join(', ');

  return (
    <div className="carry-trend">
      <span className="carry-trend__label">{t('live.carryTrend', { count: recent.length })}</span>
      <div className="carry-trend__bars" role="img" aria-label={summary}>
        {carries.map((carry, index) => (
          <span
            key={`${recent[index].timestamp}-${index}`}
            className={`carry-trend__bar${index === carries.length - 1 ? ' carry-trend__bar--latest' : ''}`}
            style={{ height: `${height(carry)}%` }}
          />
        ))}
      </div>
    </div>
  );
}
