import type { DerivedValue, Shot, SpinQuality, SwingSpeedStats } from '../../types/shot';
import { getSwingSpeedMph, isSwingSpeedShot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { isHorizontalLaunchEstimated, isSpinEstimated, isVerticalLaunchEstimated } from '../../utils/provenance';
import { getHtmlLang, t, type MessageKey } from '../../i18n';

/** Placeholder for a metric the current shot has no value for. */
export const NO_VALUE = '—';

export interface LiveMetric {
  /** Stable key. Persisted as the promoted-hero choice, so do not rename. */
  id: string;
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  /** True when the value is modeled rather than measured. Rendered as an icon. */
  estimated?: boolean;
  confidence?: SpinQuality | null;
  /** Override confidence copy while preserving its dot level. */
  confidenceLabel?: string;
}

/**
 * Live table: ten metrics, always the same ten in the same canonical order, so
 * the grid never reflows between shots. Metrics the shot did not produce render
 * as {@link NO_VALUE} rather than disappearing. The selected metric is then
 * pinned to the top-left by {@link pinSelectedMetric}.
 */
export const LIVE_METRIC_COUNT = 10;

/** Metric count for a swing-speed session: a single 5-tile row. */
export const SWING_METRIC_COUNT = 5;

function formatOptionalAngle(value: number | null, signed = false): string {
  if (value === null) return NO_VALUE;
  const prefix = signed && value >= 0 ? '+' : '';
  return `${prefix}${value.toFixed(1)}`;
}

function angleUnit(value: number | null): string | undefined {
  return value === null ? undefined : '°';
}

function formatSpinRpm(rpm: number | null): string {
  if (rpm === null) return NO_VALUE;
  return rpm.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 });
}

function launchAngleQuality(confidence: number | null): SpinQuality | null {
  if (confidence === null) return null;
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function shotShape(spinAxisDeg: number | null): string | undefined {
  if (spinAxisDeg === null) return undefined;
  if (spinAxisDeg > 2) return t('shape.fade');
  if (spinAxisDeg < -2) return t('shape.draw');
  return t('shape.straight');
}

export function liveCarryYards(shot: Shot): number {
  return shot.carry_spin_adjusted ?? shot.estimated_carry_yards;
}

function markEstimated(isEstimated: boolean): true | undefined {
  return isEstimated ? true : undefined;
}

function experimentalStatus(status: string | null | undefined): string {
  if (!status || status === 'candidate_available') return 'candidate';
  return status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ');
}

function buildBallStrikeMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);
  const carry = liveCarryYards(shot);
  const angleConfidence = launchAngleQuality(shot.launch_angle_confidence);
  const horizontalLaunchIsCameraAssisted = shot.launch_angle_horizontal_source === 'camera_assisted_experimental';
  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const attackAngle =
    shot.club_angle_deg ??
    shot.experimental_fused_attack_angle_deg ??
    (!fusedDeliveryAttempted ? shot.experimental_attack_angle_deg : null) ??
    null;
  const attackIsExperimental =
    shot.club_angle_deg === null &&
    (shot.experimental_fused_attack_angle_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_attack_angle_deg != null ||
      shot.experimental_attack_angle_status != null);
  const clubPath =
    shot.club_path_deg ??
    shot.experimental_fused_club_path_deg ??
    (!fusedDeliveryAttempted ? shot.experimental_club_path_deg : null) ??
    null;
  const clubPathIsExperimental =
    shot.club_path_deg === null &&
    (shot.experimental_fused_club_path_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_club_path_deg != null ||
      shot.experimental_club_path_status != null);

  return [
    {
      id: 'ball_speed',
      label: t('metric.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'carry',
      label: t('metric.carry'),
      value: formatDistance(carry, unitSystem, 0),
      unit: getDistanceUnit(unitSystem),
      subtext: shot.carry_spin_adjusted === null ? undefined : t('metric.spinAdjusted'),
      // Carry is always a ballistic-model output; no sensor observes it.
      estimated: true,
    },
    {
      id: 'club_speed',
      label: t('metric.clubSpeed'),
      value: shot.club_speed_mph === null ? NO_VALUE : formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: shot.club_speed_mph === null ? undefined : speedUnit,
    },
    {
      id: 'smash',
      label: t('metric.smash'),
      value: shot.smash_factor === null ? NO_VALUE : shot.smash_factor.toFixed(2),
    },
    {
      id: 'launch_v',
      label: t('metric.vLaunch'),
      value: formatOptionalAngle(shot.launch_angle_vertical),
      unit: angleUnit(shot.launch_angle_vertical),
      estimated: markEstimated(isVerticalLaunchEstimated(shot)),
      confidence: shot.launch_angle_vertical === null ? null : angleConfidence,
    },
    {
      id: 'launch_h',
      label: t('metric.hLaunch'),
      value: formatOptionalAngle(shot.launch_angle_horizontal, true),
      unit: angleUnit(shot.launch_angle_horizontal),
      subtext: horizontalLaunchIsCameraAssisted ? 'camera assisted' : undefined,
      estimated: markEstimated(isHorizontalLaunchEstimated(shot)),
      confidence: shot.launch_angle_horizontal === null ? null : angleConfidence,
      confidenceLabel: horizontalLaunchIsCameraAssisted ? 'experimental' : undefined,
    },
    {
      id: 'spin',
      label: t('metric.spin'),
      value: formatSpinRpm(shot.spin_rpm),
      unit: shot.spin_rpm === null ? undefined : 'rpm',
      estimated: markEstimated(isSpinEstimated(shot)),
      confidence: shot.spin_rpm === null ? null : shot.spin_quality,
    },
    {
      id: 'spin_axis',
      label: t('metric.spinAxis'),
      value: formatOptionalAngle(shot.spin_axis_deg, true),
      unit: angleUnit(shot.spin_axis_deg),
      subtext: shotShape(shot.spin_axis_deg),
      estimated: markEstimated(shot.spin_axis_deg !== null && shot.spin_axis_source === 'estimated'),
    },
    {
      id: 'club_path',
      label: t('metric.clubPath'),
      value: formatOptionalAngle(clubPath, true),
      unit: angleUnit(clubPath),
      subtext:
        shot.club_path_deg !== null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_club_path_deg != null
              ? 'camera fused'
              : experimentalStatus(shot.experimental_fused_status)
            : clubPathIsExperimental
              ? experimentalStatus(shot.experimental_club_path_status)
              : undefined,
      confidence: clubPathIsExperimental ? (shot.experimental_fused_club_path_confidence ?? 'experimental') : null,
      confidenceLabel: shot.experimental_fused_club_path_confidence ? 'experimental' : undefined,
    },
    {
      id: 'club_aoa',
      label: t('metric.clubAoa'),
      value: formatOptionalAngle(attackAngle, true),
      unit: angleUnit(attackAngle),
      subtext:
        shot.club_angle_deg !== null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_attack_angle_deg != null
              ? 'camera fused'
              : experimentalStatus(shot.experimental_fused_status)
            : attackIsExperimental
              ? experimentalStatus(shot.experimental_attack_angle_status)
              : undefined,
      confidence: attackIsExperimental ? (shot.experimental_fused_attack_angle_confidence ?? 'experimental') : null,
      confidenceLabel: shot.experimental_fused_attack_angle_confidence ? 'experimental' : undefined,
    },
  ];
}

function buildSwingSpeedMetrics(shot: Shot, stats: SwingSpeedStats, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);

  return [
    {
      id: 'swing_last',
      label: t('metric.lastSwing'),
      value: formatSpeed(getSwingSpeedMph(shot), unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'swing_best',
      label: t('metric.best'),
      value: formatSpeed(stats.best_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.profileImplement'),
    },
    {
      id: 'swing_avg',
      label: t('metric.average'),
      value: formatSpeed(stats.avg_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.swingsCount', { count: stats.count }),
    },
    {
      id: 'swing_count',
      label: t('metric.swings'),
      value: String(stats.count),
      subtext:
        shot.swing_speed_reading_count === undefined
          ? undefined
          : t('metric.readingsCount', { count: shot.swing_speed_reading_count }),
    },
    {
      id: 'swing_implement',
      label: t('metric.implement'),
      value: shot.training_implement_label ?? shot.club,
      subtext:
        shot.swing_speed_trigger_mph === undefined
          ? undefined
          : t('metric.trigger', {
              speed: formatSpeed(shot.swing_speed_trigger_mph, unitSystem, 1),
              unit: speedUnit,
            }),
    },
  ];
}

/**
 * Build the fixed metric list for the Live panel. Returns {@link LIVE_METRIC_COUNT}
 * entries for a ball-strike shot and {@link SWING_METRIC_COUNT} for a swing-speed
 * one; the two sets share no ids, so a selected-metric choice never leaks across
 * modes.
 */
export function buildLiveMetrics(shot: Shot, unitSystem: UnitSystem, swingStats: SwingSpeedStats): LiveMetric[] {
  return isSwingSpeedShot(shot)
    ? buildSwingSpeedMetrics(shot, swingStats, unitSystem)
    : buildBallStrikeMetrics(shot, unitSystem);
}

/**
 * Put the selected metric first (top-left of the table) and keep the rest in
 * canonical order. Falls back to the first metric when `selectedId` is absent
 * from this list — which is the normal case right after switching between
 * ball-strike and swing-speed modes.
 */
export function pinSelectedMetric(metrics: LiveMetric[], selectedId: string | null): LiveMetric[] {
  if (metrics.length === 0) {
    return [];
  }

  const selectedIndex = metrics.findIndex((metric) => metric.id === selectedId);
  const index = selectedIndex === -1 ? 0 : selectedIndex;

  return [metrics[index], ...metrics.filter((_, i) => i !== index)];
}

/** Prefix shared by every derived-metric id so they never collide with the base ten. */
const DERIVED_ID_PREFIX = 'derived_';

/** Derived metrics that may be promoted to the hero slot (only with "More metrics" on). */
export const DERIVED_HERO_IDS: readonly string[] = ['derived_total', 'derived_shot_shape'];

const SHOT_SHAPE_LABELS: Record<string, MessageKey> = {
  straight: 'shape.straight',
  fade: 'shape.fade',
  draw: 'shape.draw',
  slice: 'shape.slice',
  hook: 'shape.hook',
  push: 'shape.push',
  pull: 'shape.pull',
  'push-fade': 'shape.pushFade',
  'push-draw': 'shape.pushDraw',
  'pull-fade': 'shape.pullFade',
  'pull-draw': 'shape.pullDraw',
};

/** Localized word for a server shot-shape id; unknown ids show as sent. */
export function shotShapeLabel(shape: string): string {
  const key = SHOT_SHAPE_LABELS[shape];
  return key ? t(key) : shape;
}

type DerivedKind = 'distance' | 'signedDistance' | 'seconds' | 'angle' | 'signedAngle';

interface DerivedSpec {
  id: string;
  key: string;
  label: MessageKey;
  kind: DerivedKind;
}

/** Card order for the derived strip. Every card is optional: absent keys render nothing. */
const DERIVED_SPECS: readonly DerivedSpec[] = [
  { id: 'derived_total', key: 'total_yards', label: 'metric.total', kind: 'distance' },
  { id: 'derived_roll', key: 'roll_yards', label: 'metric.roll', kind: 'distance' },
  { id: 'derived_apex', key: 'apex_yards', label: 'flight.apex', kind: 'distance' },
  { id: 'derived_hang_time', key: 'hang_time_s', label: 'flight.hangTime', kind: 'seconds' },
  { id: 'derived_landing_angle', key: 'landing_angle_deg', label: 'flight.landingAngle', kind: 'angle' },
  { id: 'derived_curve', key: 'curve_yards', label: 'metric.curve', kind: 'signedDistance' },
  { id: 'derived_side', key: 'side_yards', label: 'metric.side', kind: 'signedDistance' },
  { id: 'derived_face_to_path', key: 'face_to_path_deg', label: 'metric.faceToPath', kind: 'signedAngle' },
  { id: 'derived_spin_loft', key: 'spin_loft_deg', label: 'metric.spinLoft', kind: 'angle' },
];

function formatSignedDistance(yards: number, unitSystem: UnitSystem): string {
  const text = formatDistance(Math.abs(yards), unitSystem, 0);
  if (yards === 0) return text;
  return `${yards > 0 ? '+' : '-'}${text}`;
}

function numericDerivedMetric(
  spec: DerivedSpec,
  entry: DerivedValue,
  value: number,
  unitSystem: UnitSystem
): LiveMetric {
  const estimated = markEstimated(entry.source === 'estimated');
  switch (spec.kind) {
    case 'distance':
      return {
        id: spec.id,
        label: t(spec.label),
        value: formatDistance(value, unitSystem, 0),
        unit: getDistanceUnit(unitSystem),
        estimated,
      };
    case 'signedDistance':
      return {
        id: spec.id,
        label: t(spec.label),
        value: formatSignedDistance(value, unitSystem),
        unit: getDistanceUnit(unitSystem),
        estimated,
      };
    case 'seconds':
      return { id: spec.id, label: t(spec.label), value: value.toFixed(1), unit: 's', estimated };
    case 'angle':
      return { id: spec.id, label: t(spec.label), value: formatOptionalAngle(value), unit: '°', estimated };
    case 'signedAngle':
      return { id: spec.id, label: t(spec.label), value: formatOptionalAngle(value, true), unit: '°', estimated };
  }
}

/**
 * Compact cards for the server's `derived` block, in a fixed order, skipping any
 * key the shot lacks. Units follow the unit preference like the base metrics.
 * Returns an empty list for a shot without `derived` so the strip stays hidden.
 */
export function buildDerivedMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const derived = shot.derived;
  if (!derived || isSwingSpeedShot(shot)) {
    return [];
  }

  const metrics: LiveMetric[] = [];
  for (const spec of DERIVED_SPECS) {
    const entry = derived[spec.key];
    if (entry && typeof entry.value === 'number' && Number.isFinite(entry.value)) {
      metrics.push(numericDerivedMetric(spec, entry, entry.value, unitSystem));
    }
  }

  const shape = derived.shot_shape;
  if (shape && typeof shape.value === 'string' && shape.value !== '') {
    metrics.push({
      id: 'derived_shot_shape',
      label: t('metric.shotShape'),
      value: shotShapeLabel(shape.value),
      estimated: markEstimated(shape.source === 'estimated'),
    });
  }

  return metrics;
}

export function isDerivedMetricId(id: string | null): boolean {
  return id !== null && id.startsWith(DERIVED_ID_PREFIX);
}

/**
 * The metric the post-shot cue (big number, voice) speaks about. A derived hero
 * choice counts only while "More metrics" is on and the shot carries that value;
 * otherwise the base table's pinned metric wins, exactly as before.
 */
export function heroMetric(
  shot: Shot,
  unitSystem: UnitSystem,
  swingStats: SwingSpeedStats,
  heroMetricId: string | null,
  moreMetrics: boolean
): LiveMetric | null {
  if (moreMetrics && heroMetricId !== null && DERIVED_HERO_IDS.includes(heroMetricId)) {
    const derived = buildDerivedMetrics(shot, unitSystem).find((metric) => metric.id === heroMetricId);
    if (derived) {
      return derived;
    }
  }
  return pinSelectedMetric(buildLiveMetrics(shot, unitSystem, swingStats), heroMetricId)[0] ?? null;
}
