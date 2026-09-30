import { NO_VALUE, type LiveMetric } from './panel/liveMetrics';

export const TAKEOVER_DURATION_MS = 2500;

export function hasCalloutValue(metric: LiveMetric | null): metric is LiveMetric {
  return metric !== null && metric.value !== NO_VALUE;
}

export interface ShotCue {
  // The shot store bumps shotVersion only for a new shot, never for shot_update.
  seenVersion: number;
  takeoverVersion: number | null;
}

export interface ShotCueInput {
  shotVersion: number;
  isNewShot: boolean;
  metric: LiveMetric | null;
  bigNumberAfterShot: boolean;
  liveView: boolean;
}

export function nextShotCue(cue: ShotCue, input: ShotCueInput): ShotCue {
  if (input.shotVersion === cue.seenVersion) {
    return cue;
  }

  const show = input.bigNumberAfterShot && input.liveView && input.isNewShot && hasCalloutValue(input.metric);
  return { seenVersion: input.shotVersion, takeoverVersion: show ? input.shotVersion : null };
}
