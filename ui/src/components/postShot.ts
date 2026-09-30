import { t, type MessageKey } from '../i18n';
import { NO_VALUE, type LiveMetric } from './panel/liveMetrics';

export const TAKEOVER_DURATION_MS = 2500;

export function hasCalloutValue(metric: LiveMetric | null): metric is LiveMetric {
  return metric !== null && metric.value !== NO_VALUE;
}

const SPOKEN_UNITS: Record<string, MessageKey> = {
  mph: 'voice.mph',
  'km/h': 'voice.kmh',
  yds: 'voice.yards',
  m: 'voice.meters',
  '°': 'voice.degrees',
  rpm: 'voice.rpm',
};

/** Spin is already localized with grouping ("2.328" in pt-BR); every other value is `toFixed` with a '.' decimal. */
const GROUPED_INTEGER = /^[+-]?\d{1,3}(\.\d{3})+$/;

function spokenNumber(value: string, decimal: string): string {
  if (decimal === '.') return value;
  if (GROUPED_INTEGER.test(value)) return value.replaceAll('.', '');
  return value.replace('.', decimal);
}

/** Value and unit as words for speech in the active language, or null when there is nothing to say. */
export function calloutText(metric: LiveMetric | null, lang: string): string | null {
  if (!hasCalloutValue(metric)) {
    return null;
  }

  const decimal = new Intl.NumberFormat(lang).formatToParts(1.5).find((part) => part.type === 'decimal')?.value;
  const value = decimal ? spokenNumber(metric.value, decimal) : metric.value;
  const unitKey = metric.unit ? SPOKEN_UNITS[metric.unit] : undefined;
  const unit = unitKey ? t(unitKey) : (metric.unit ?? '');
  return `${value} ${unit}`.trim();
}

export interface ShotCue {
  // The shot store bumps shotVersion only for a new shot, never for shot_update.
  seenVersion: number;
  takeoverVersion: number | null;
  callout: string | null;
}

export interface ShotCueInput {
  shotVersion: number;
  isNewShot: boolean;
  metric: LiveMetric | null;
  bigNumberAfterShot: boolean;
  voiceCallout: boolean;
  lang: string;
  liveView: boolean;
}

export function initialShotCue(shotVersion: number): ShotCue {
  return { seenVersion: shotVersion, takeoverVersion: null, callout: null };
}

export function nextShotCue(cue: ShotCue, input: ShotCueInput): ShotCue {
  if (input.shotVersion === cue.seenVersion) {
    return cue;
  }

  const show = input.bigNumberAfterShot && input.liveView && input.isNewShot && hasCalloutValue(input.metric);
  return {
    seenVersion: input.shotVersion,
    takeoverVersion: show ? input.shotVersion : null,
    callout: input.voiceCallout && input.isNewShot ? calloutText(input.metric, input.lang) : null,
  };
}
