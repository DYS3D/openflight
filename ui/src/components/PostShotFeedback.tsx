import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Shot } from '../types/shot';
import { computeSwingSpeedStats, filterShotsByProfile } from '../types/shot';
import { useUnitPreference } from '../state/useUnitPreference';
import { useDisplayPreferencesStore } from '../stores/useDisplayPreferencesStore';
import { applySharedFitFontSize } from '../hooks/useFitFontSize';
import { buildLiveMetrics, pinSelectedMetric, type LiveMetric } from './panel/liveMetrics';
import { EstimatedMark } from './ui/MetricCard';
import { nextShotCue, TAKEOVER_DURATION_MS, type ShotCue } from './postShot';
import './PostShotFeedback.css';

interface PostShotTakeoverProps {
  metric: LiveMetric;
  onDismiss: () => void;
}

export function PostShotTakeover({ metric, onDismiss }: PostShotTakeoverProps) {
  const rowRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    if (rowRef.current) {
      applySharedFitFontSize([rowRef.current]);
    }
  }, [metric.value, metric.unit]);

  return (
    <button type="button" className="post-shot-takeover" onClick={onDismiss}>
      <span className="post-shot-takeover__label">
        {metric.label}
        {metric.estimated ? <EstimatedMark /> : null}
      </span>
      <span ref={rowRef} className="post-shot-takeover__value-row">
        <span className="post-shot-takeover__value">{metric.value}</span>
        {metric.unit ? <span className="post-shot-takeover__unit">{metric.unit}</span> : null}
      </span>
    </button>
  );
}

interface PostShotFeedbackProps {
  shot: Shot | null;
  shots: Shot[];
  profileId: string;
  activeTrainingImplement?: string;
  heroMetricId: string | null;
  shotVersion: number;
  isNewShot: boolean;
  liveView: boolean;
  /** Omit to read the display preferences store; pass it in tests. */
  bigNumberAfterShot?: boolean;
}

export function PostShotFeedback({
  shot,
  shots,
  profileId,
  activeTrainingImplement,
  heroMetricId,
  shotVersion,
  isNewShot,
  liveView,
  bigNumberAfterShot: bigNumberAfterShotProp,
}: PostShotFeedbackProps) {
  const storeBigNumber = useDisplayPreferencesStore((state) => state.preferences.bigNumberAfterShot);
  const bigNumberAfterShot = bigNumberAfterShotProp ?? storeBigNumber;
  const { unitSystem } = useUnitPreference();

  let metric: LiveMetric | null = null;
  if (shot) {
    const swingStats = computeSwingSpeedStats(filterShotsByProfile(shots, profileId), {
      profileId,
      trainingImplement: activeTrainingImplement,
    });
    metric = pinSelectedMetric(buildLiveMetrics(shot, unitSystem, swingStats), heroMetricId)[0] ?? null;
  }

  const [cue, setCue] = useState<ShotCue>({ seenVersion: shotVersion, takeoverVersion: null });
  const nextCue = nextShotCue(cue, { shotVersion, isNewShot, metric, bigNumberAfterShot, liveView });
  if (nextCue !== cue) {
    setCue(nextCue);
  }

  useEffect(() => {
    if (cue.takeoverVersion === null) {
      return undefined;
    }
    const timer = window.setTimeout(
      () => setCue((current) => ({ ...current, takeoverVersion: null })),
      TAKEOVER_DURATION_MS
    );
    return () => window.clearTimeout(timer);
  }, [cue.takeoverVersion]);

  if (nextCue.takeoverVersion === null || !liveView || !metric) {
    return null;
  }

  return <PostShotTakeover metric={metric} onDismiss={() => setCue({ ...nextCue, takeoverVersion: null })} />;
}
