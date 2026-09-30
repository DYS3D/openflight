import { describe, expect, it } from 'vitest';
import type { LiveMetric } from './panel/liveMetrics';
import { hasCalloutValue, nextShotCue, type ShotCue, type ShotCueInput } from './postShot';

const carry: LiveMetric = { id: 'carry', label: 'Carry', value: '214', unit: 'yds', estimated: true };

function input(overrides: Partial<ShotCueInput> = {}): ShotCueInput {
  return {
    shotVersion: 1,
    isNewShot: true,
    metric: carry,
    bigNumberAfterShot: true,
    liveView: true,
    ...overrides,
  };
}

const idle: ShotCue = { seenVersion: 0, takeoverVersion: null };

describe('nextShotCue', () => {
  it('shows the takeover for a newly captured shot when the preference is on', () => {
    expect(nextShotCue(idle, input())).toEqual({ seenVersion: 1, takeoverVersion: 1 });
  });

  it('shows nothing with the preference off, but still records the shot as seen', () => {
    expect(nextShotCue(idle, input({ bigNumberAfterShot: false }))).toEqual({ seenVersion: 1, takeoverVersion: null });
  });

  it('keeps the same cue for a shot_update refinement (same version, new values)', () => {
    const showing = nextShotCue(idle, input());
    const refined = nextShotCue(showing, input({ metric: { ...carry, value: '216' } }));

    expect(refined).toBe(showing);

    const dismissed: ShotCue = { ...showing, takeoverVersion: null };
    expect(nextShotCue(dismissed, input({ metric: { ...carry, value: '216' } }))).toBe(dismissed);
  });

  it('does not show on other views, for restored shots, or without a value', () => {
    expect(nextShotCue(idle, input({ liveView: false })).takeoverVersion).toBeNull();
    expect(nextShotCue(idle, input({ isNewShot: false })).takeoverVersion).toBeNull();
    expect(nextShotCue(idle, input({ metric: null })).takeoverVersion).toBeNull();
    expect(nextShotCue(idle, input({ metric: { ...carry, value: '—', unit: undefined } })).takeoverVersion).toBeNull();
  });

  it('replaces a showing takeover with the next shot', () => {
    const first = nextShotCue(idle, input());

    expect(nextShotCue(first, input({ shotVersion: 2 }))).toEqual({ seenVersion: 2, takeoverVersion: 2 });
  });
});

describe('hasCalloutValue', () => {
  it('rejects the no-value placeholder', () => {
    expect(hasCalloutValue(carry)).toBe(true);
    expect(hasCalloutValue({ ...carry, value: '—' })).toBe(false);
    expect(hasCalloutValue(null)).toBe(false);
  });
});
