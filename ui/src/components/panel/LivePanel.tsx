import { useMemo, useRef, type ReactNode } from 'react';
import type { Shot } from '../../types/shot';
import { computeSwingSpeedStats, filterShotsByProfile } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { formatDistance, getDistanceUnit } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { useSharedFitFontSize } from '../../hooks/useFitFontSize';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useDisplayPreferencesStore } from '../../stores/useDisplayPreferencesStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';
import {
  buildDerivedMetrics,
  buildLiveMetrics,
  DERIVED_HERO_IDS,
  LIVE_METRIC_COUNT,
  pinSelectedMetric,
} from './liveMetrics';
import { consistencyBands } from './consistency';
import { CarryTrend } from './CarryTrend';
import { rangeStatus } from './launchWindows';

interface LivePanelProps {
  shot: Shot | null;
  shots: Shot[];
  profileId: string;
  profileName: string;
  clubLabel: string;
  /** Undefined outside swing-speed mode. Scopes the swing stats to one implement. */
  activeTrainingImplement?: string;
  /** Metric pinned top-left while the full table remains visible. */
  selectedMetricId?: string | null;
  onSelectMetric?: (id: string) => void;
  /** True for a freshly captured shot (not a restored session). */
  isNewShot?: boolean;
  /** Pinned header control, e.g. Change club. */
  headerAction?: ReactNode;
  /** Omit these to read the display preferences store; pass them in tests. */
  consistencyColors?: boolean;
  showNormalizedCarry?: boolean;
  moreMetrics?: boolean;
  /** Makes the profile name a button that opens the golfer picker. */
  onSwitchProfile?: () => void;
  /** Copper tile layout (hero tiles, carry trend, range markers). Omit to follow the theme. */
  copperLayout?: boolean;
}

/**
 * Ten-metric table. Tapping a tile selects it: the title turns accent yellow
 * and the tile moves to the top-left without hiding the remaining metrics.
 */
export function LivePanel({
  shot,
  shots,
  profileId,
  profileName,
  clubLabel,
  activeTrainingImplement,
  selectedMetricId = null,
  onSelectMetric,
  isNewShot = false,
  headerAction,
  consistencyColors: consistencyColorsProp,
  showNormalizedCarry: showNormalizedCarryProp,
  moreMetrics: moreMetricsProp,
  onSwitchProfile,
  copperLayout: copperLayoutProp,
}: LivePanelProps) {
  const { locale, t } = useI18n();
  const { unitSystem } = useUnitPreference();
  const storePreferences = useDisplayPreferencesStore((state) => state.preferences);
  const consistencyColors = consistencyColorsProp ?? storePreferences.consistencyColors;
  const showNormalizedCarry = showNormalizedCarryProp ?? storePreferences.showNormalizedCarry;
  const moreMetrics = moreMetricsProp ?? storePreferences.moreMetrics;
  const themeIsCopper = useThemeStore((state) => state.theme === 'copper');
  const copperLayout = copperLayoutProp ?? themeIsCopper;
  const stripRef = useRef<HTMLDivElement>(null);
  const stripScroll = useDragScroll(stripRef, 'x');
  const profileShots = useMemo(() => filterShotsByProfile(shots, profileId), [shots, profileId]);
  const displayedShot = profileShots[profileShots.length - 1] ?? null;
  const isProfileNewShot = Boolean(isNewShot && shot && displayedShot && shot.timestamp === displayedShot.timestamp);

  const swingStats = useMemo(
    () => computeSwingSpeedStats(profileShots, { profileId, trainingImplement: activeTrainingImplement }),
    [profileShots, profileId, activeTrainingImplement]
  );
  const metrics = useMemo(
    () =>
      displayedShot ? pinSelectedMetric(buildLiveMetrics(displayedShot, unitSystem, swingStats), selectedMetricId) : [],
    // locale is not read in the factory; t() is a stable import. Without it,
    // changing language would keep stale metric labels.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see above
    [displayedShot, unitSystem, swingStats, selectedMetricId, locale]
  );
  const derivedMetrics = useMemo(
    () => (moreMetrics && displayedShot ? buildDerivedMetrics(displayedShot, unitSystem) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- locale: see above
    [moreMetrics, displayedShot, unitSystem, locale]
  );
  const bands = useMemo(
    () => (consistencyColors && displayedShot ? consistencyBands(displayedShot, profileShots, profileId) : {}),
    [consistencyColors, displayedShot, profileShots, profileId]
  );
  const normalizedCarry = displayedShot?.carry_normalized_yards;
  const normalizedCarryText =
    showNormalizedCarry && typeof normalizedCarry === 'number'
      ? t('metric.normalizedCarry', {
          value: formatDistance(normalizedCarry, unitSystem, 0),
          unit: getDistanceUnit(unitSystem),
        })
      : undefined;
  const selected = metrics[0] ?? null;
  const metricRange = (id: string) => {
    if (!copperLayout || !displayedShot) return undefined;
    if (id === 'launch_v') return rangeStatus(id, displayedShot.launch_angle_vertical, displayedShot.club);
    if (id === 'spin') return rangeStatus(id, displayedShot.spin_rpm, displayedShot.club);
    return undefined;
  };
  // A derived hero (Total, Shot shape) lives in the strip; the base table then has no highlight.
  const derivedHeroId =
    selectedMetricId !== null &&
    DERIVED_HERO_IDS.includes(selectedMetricId) &&
    derivedMetrics.some((metric) => metric.id === selectedMetricId)
      ? selectedMetricId
      : null;
  const selectedId = derivedHeroId ?? selected?.id ?? null;
  const gridRef = useSharedFitFontSize(
    metrics.length > 0,
    metrics.map((metric) => `${metric.value}:${metric.unit ?? ''}`).join('|')
  );
  const subtitle =
    onSwitchProfile && profileName ? (
      <button
        type="button"
        className="live-panel__golfer"
        aria-haspopup="dialog"
        aria-label={t('live.switchGolfer', { name: profileName })}
        onClick={onSwitchProfile}
      >
        {profileName}
      </button>
    ) : (
      profileName
    );
  const header = <PanelHeader title={t('nav.live')} subtitle={subtitle} club={clubLabel} actions={headerAction} />;

  if (!selected) {
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty live-panel__empty">
          <span className="panel__empty-title live-panel__empty-title">{t('live.ready')}</span>
          <span className="panel__empty-detail live-panel__empty-detail">{t('live.readyDetail')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {header}
      <div
        className={`panel__body live-panel__body${derivedMetrics.length > 0 ? ' live-panel__body--with-derived' : ''}`}
      >
        {isProfileNewShot ? <div className="shot-flash" /> : null}
        <div
          ref={gridRef}
          className={`live-panel__grid live-panel__grid--of-${metrics.length}${copperLayout ? ' live-panel__grid--copper' : ''}`}
        >
          {metrics.map((metric, index) => (
            <MetricCard
              key={metric.id}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              subtext={metric.subtext}
              detail={metric.id === 'carry' ? normalizedCarryText : undefined}
              estimated={metric.estimated}
              confidence={metric.confidence}
              confidenceLabel={metric.confidenceLabel}
              consistency={bands[metric.id]}
              range={metricRange(metric.id)}
              fitGroup={copperLayout && metrics.length === LIVE_METRIC_COUNT && index < 2 ? 'hero' : undefined}
              aside={
                copperLayout && metric.id === 'carry' && profileShots.length >= 2 ? (
                  <CarryTrend shots={profileShots} unitSystem={unitSystem} />
                ) : undefined
              }
              labelPosition="above"
              selected={metric.id === selectedId}
              onClick={onSelectMetric ? () => onSelectMetric(metric.id) : undefined}
            />
          ))}
        </div>
        {derivedMetrics.length > 0 ? (
          <div
            ref={stripRef}
            className="live-panel__derived"
            role="group"
            aria-label={t('menu.moreMetrics')}
            onPointerDown={stripScroll.onPointerDown}
            onPointerMove={stripScroll.onPointerMove}
            onPointerUp={stripScroll.onPointerUp}
            onPointerCancel={stripScroll.onPointerCancel}
            onClickCapture={stripScroll.onClickCapture}
          >
            {derivedMetrics.map((metric) => {
              const promotable = onSelectMetric !== undefined && DERIVED_HERO_IDS.includes(metric.id);
              return (
                <MetricCard
                  key={metric.id}
                  label={metric.label}
                  value={metric.value}
                  unit={metric.unit}
                  estimated={metric.estimated}
                  labelPosition="above"
                  selected={promotable ? metric.id === selectedId : undefined}
                  onClick={promotable ? () => onSelectMetric(metric.id) : undefined}
                />
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
