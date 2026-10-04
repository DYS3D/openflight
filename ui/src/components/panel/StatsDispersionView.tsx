import { useMemo, useState } from 'react';
import type { Shot } from '../../types/shot';
import { ballShots, buildDispersion, clubSeries, latestLanding } from '../../utils/shotAnalysis';
import { getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { DispersionChart, DispersionLegend } from '../charts/DispersionChart';

interface StatsDispersionViewProps {
  shots: Shot[];
  unitSystem: UnitSystem;
  /** For tests: renderToString cannot tap. */
  initialHighlight?: string | null;
}

export function StatsDispersionView({ shots, unitSystem, initialHighlight = null }: StatsDispersionViewProps) {
  const { t } = useI18n();
  const groups = useMemo(() => buildDispersion(shots), [shots]);
  const series = useMemo(() => clubSeries(shots), [shots]);
  const latest = useMemo(() => latestLanding(ballShots(shots)), [shots]);
  const [highlighted, setHighlighted] = useState<string | null>(initialHighlight);
  const activeHighlight = groups.some((group) => group.club === highlighted) ? highlighted : null;

  if (groups.length === 0) {
    return (
      <div className="panel__body--empty">
        <span className="panel__empty-title">{t('dispersion.noData')}</span>
        <span className="panel__empty-detail">{t('dispersion.noDataDetail')}</span>
      </div>
    );
  }

  return (
    <div className="stats-dispersion">
      <div className="stats-dispersion__bar">
        <DispersionLegend
          groups={groups}
          series={series}
          highlightedClub={activeHighlight}
          onToggleClub={(club) => setHighlighted((current) => (current === club ? null : club))}
        />
        <span className="stats-dispersion__note">{t('dispersion.ellipseNote')}</span>
      </div>
      <DispersionChart
        groups={groups}
        series={series}
        highlightedClub={activeHighlight}
        unitSystem={unitSystem}
        caption={t('dispersion.caption', { unit: getDistanceUnit(unitSystem) })}
        ariaLabel={t('dispersion.aria')}
        latest={latest}
      />
    </div>
  );
}
