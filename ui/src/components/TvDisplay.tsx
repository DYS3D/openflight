import { useEffect, useMemo } from 'react';
import type { Shot } from '../types/shot';
import { filterShotsByProfile } from '../types/shot';
import { ballShots, buildDispersion, carryYards, clubSeries, flightExtent, flightTraces } from '../utils/shotAnalysis';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit, type UnitSystem } from '../utils/units';
import { useUnitPreference } from '../state/useUnitPreference';
import { useI18n } from '../i18n/useI18n';
import { getClubName } from '../data/clubs';
import { applyTheme } from '../theme/theme';
import { FlightChart, FlightKey } from './charts/FlightChart';
import { LivePanel } from './panel';
import { DispersionChart, DispersionLegend } from './charts/DispersionChart';
import './TvDisplay.css';

const RECENT_SHOT_COUNT = 5;

interface TvDisplayProps {
  connected: boolean;
  shots: Shot[];
  profileId: string;
  profileName: string;
  /** Omit to read the store; tests pass it (renderToString sees the initial state). */
  unitSystem?: UnitSystem;
}

/**
 * `/display?layout=tv`: the kiosk's copper Live screen (all ten metrics) for a TV
 * across the bay, with the latest flight, dispersion and the last five shots.
 */
export function TvDisplay({ connected, shots, profileId, profileName, unitSystem: unitSystemProp }: TvDisplayProps) {
  const { t } = useI18n();
  const storeUnitSystem = useUnitPreference().unitSystem;
  const unitSystem = unitSystemProp ?? storeUnitSystem;
  const profileShots = useMemo(() => ballShots(filterShotsByProfile(shots, profileId)), [shots, profileId]);
  const traces = useMemo(() => flightTraces(profileShots), [profileShots]);
  const groups = useMemo(() => buildDispersion(profileShots), [profileShots]);
  const series = useMemo(() => clubSeries(profileShots), [profileShots]);
  const latest = profileShots[profileShots.length - 1] ?? null;
  const recent = profileShots.slice(-RECENT_SHOT_COUNT).reverse();
  const distanceUnit = getDistanceUnit(unitSystem);
  const speedUnit = getSpeedUnit(unitSystem);

  // The TV always wears Copperline's copper look, whatever its browser last stored.
  useEffect(() => applyTheme('copper'), []);

  return (
    <main className="tv-display" aria-label={t('tv.aria')}>
      <section className="tv-display__live">
        <LivePanel
          shot={latest}
          shots={shots}
          profileId={profileId}
          profileName={profileName || t('display.eyebrow')}
          clubLabel={latest ? getClubName(latest.club) : t('display.ready')}
          copperLayout
        />
        {!connected ? <span className="tv-display__offline">{t('display.socketOff')}</span> : null}
      </section>

      <section className="tv-display__panel tv-display__flight" aria-label={t('tv.flight')}>
        {traces ? (
          <FlightChart
            view="side"
            latest={traces.latest.flight}
            ghosts={traces.ghosts.map((shot) => shot.flight)}
            extent={flightExtent([traces.latest.flight, ...traces.ghosts.map((shot) => shot.flight)])}
            unitSystem={unitSystem}
            caption={t('flight.sideView', { unit: distanceUnit })}
            ariaLabel={t('flight.sideViewAria')}
            legend={<FlightKey ghostCount={traces.ghosts.length} />}
          />
        ) : (
          <span className="tv-display__empty">{t('flight.noData')}</span>
        )}
      </section>

      <section className="tv-display__panel tv-display__dispersion" aria-label={t('stats.viewDispersion')}>
        {groups.length > 0 ? (
          <>
            <DispersionChart
              groups={groups}
              series={series}
              highlightedClub={null}
              unitSystem={unitSystem}
              caption={t('dispersion.caption', { unit: distanceUnit })}
              ariaLabel={t('dispersion.aria')}
            />
            <DispersionLegend groups={groups} series={series} highlightedClub={null} />
          </>
        ) : (
          <span className="tv-display__empty">{t('dispersion.noData')}</span>
        )}
      </section>

      <section className="tv-display__recent" aria-label={t('display.recentAria')}>
        {recent.length === 0 ? (
          <span className="tv-display__empty">{t('display.recentEmpty')}</span>
        ) : (
          recent.map((shot) => (
            <div key={shot.timestamp} className="tv-display__shot">
              <span className="tv-display__shot-club">{getClubName(shot.club)}</span>
              <span className="tv-display__shot-stat">
                {formatSpeed(shot.ball_speed_mph, unitSystem, 0)}
                <span className="tv-display__unit">{speedUnit}</span>
              </span>
              <span className="tv-display__shot-stat tv-display__shot-stat--carry">
                {formatDistance(carryYards(shot), unitSystem, 0)}
                <span className="tv-display__unit">{distanceUnit}</span>
              </span>
            </div>
          ))
        )}
      </section>
    </main>
  );
}
