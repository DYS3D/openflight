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

/** Value and unit as words for speech, or null when there is nothing to say. */
export function calloutText(metric: LiveMetric | null): string | null {
  if (!hasCalloutValue(metric)) {
    return null;
  }

  const unitKey = metric.unit ? SPOKEN_UNITS[metric.unit] : undefined;
  const unit = unitKey ? t(unitKey) : (metric.unit ?? '');
  return `${metric.value} ${unit}`.trim();
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
    callout: input.voiceCallout && input.isNewShot ? calloutText(input.metric) : null,
  };
}
