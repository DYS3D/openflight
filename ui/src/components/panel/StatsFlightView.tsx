import { useMemo } from 'react';
import type { Shot, ShotFlight } from '../../types/shot';
import { flightExtent, flightTraces } from '../../utils/shotAnalysis';
import { formatDistance, getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { FlightChart, FlightKey } from '../charts/FlightChart';

interface Readout {
  id: string;
  label: string;
  value: string;
  unit?: string;
}

type Translate = ReturnType<typeof useI18n>['t'];

function flightReadouts(flight: ShotFlight, unitSystem: UnitSystem, t: Translate): Readout[] {
  const unit = getDistanceUnit(unitSystem);
  const side = flight.lateral_yards < 0 ? t('chart.left') : t('chart.right');
  return [
    { id: 'carry', label: t('metric.carry'), value: formatDistance(flight.carry_yards, unitSystem, 0), unit },
    { id: 'apex', label: t('flight.apex'), value: formatDistance(flight.apex_yards, unitSystem, 0), unit },
    {
      id: 'lateral',
      label: t('flight.lateral'),
      value: formatDistance(Math.abs(flight.lateral_yards), unitSystem, 0),
      unit: Math.round(Math.abs(flight.lateral_yards)) === 0 ? unit : `${unit} ${side}`,
    },
    { id: 'landing', label: t('flight.landingAngle'), value: flight.landing_angle_deg.toFixed(0), unit: '°' },
    { id: 'hang', label: t('flight.hangTime'), value: flight.flight_time_s.toFixed(1), unit: 's' },
  ];
}

export function StatsFlightView({ shots, unitSystem }: { shots: Shot[]; unitSystem: UnitSystem }) {
  const { t } = useI18n();
  const traces = useMemo(() => flightTraces(shots), [shots]);

  if (!traces) {
    return (
      <div className="panel__body--empty">
        <span className="panel__empty-title">{t('flight.noData')}</span>
        <span className="panel__empty-detail">{t('flight.noDataDetail')}</span>
      </div>
    );
  }

  const latest = traces.latest.flight;
  const ghosts = traces.ghosts.map((shot) => shot.flight);
  const extent = flightExtent([latest, ...ghosts]);
  const unit = getDistanceUnit(unitSystem);

  return (
    <div className="stats-flight">
      <div className="stats-flight__summary">
        <span className="stats-flight__club">{traces.latest.club}</span>
        {flightReadouts(latest, unitSystem, t).map((readout) => (
          <span key={readout.id} className="stats-readout">
            <span className="stats-readout__label">{readout.label}</span>
            <span className="stats-readout__value">
              {readout.value}
              {readout.unit ? <span className="stats-readout__unit">{readout.unit}</span> : null}
            </span>
          </span>
        ))}
      </div>
      <FlightChart
        view="side"
        latest={latest}
        ghosts={ghosts}
        extent={extent}
        unitSystem={unitSystem}
        caption={t('flight.sideView', { unit })}
        ariaLabel={t('flight.sideViewAria')}
        legend={<FlightKey ghostCount={ghosts.length} />}
      />
      <FlightChart
        view="top"
        latest={latest}
        ghosts={ghosts}
        extent={extent}
        unitSystem={unitSystem}
        caption={t('flight.topView', { unit })}
        ariaLabel={t('flight.topViewAria')}
      />
    </div>
  );
}
