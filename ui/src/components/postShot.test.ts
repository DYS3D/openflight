import { describe, expect, it } from 'vitest';
import type { LiveMetric } from './panel/liveMetrics';
import { calloutText, hasCalloutValue, initialShotCue, nextShotCue, type ShotCueInput } from './postShot';

const carry: LiveMetric = { id: 'carry', label: 'Carry', value: '214', unit: 'yds', estimated: true };

function input(overrides: Partial<ShotCueInput> = {}): ShotCueInput {
  return {
    shotVersion: 1,
    isNewShot: true,
    metric: carry,
    bigNumberAfterShot: true,
    voiceCallout: false,
    liveView: true,
    ...overrides,
  };
}

const idle = initialShotCue(0);

describe('nextShotCue', () => {
  it('shows the takeover for a newly captured shot when the preference is on', () => {
    expect(nextShotCue(idle, input())).toEqual({ seenVersion: 1, takeoverVersion: 1, callout: null });
  });

  it('does nothing with every preference off, but still records the shot as seen', () => {
    expect(nextShotCue(idle, input({ bigNumberAfterShot: false }))).toEqual({
      seenVersion: 1,
      takeoverVersion: null,
      callout: null,
    });
  });

  it('keeps the same cue for a shot_update refinement (same version, new values)', () => {
    const showing = nextShotCue(idle, input({ voiceCallout: true }));
    const refined = nextShotCue(showing, input({ voiceCallout: true, metric: { ...carry, value: '216' } }));

    expect(refined).toBe(showing);
    expect(refined.callout).toBe('214 yards');

    const dismissed = { ...showing, takeoverVersion: null };
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

    expect(nextShotCue(first, input({ shotVersion: 2 }))).toMatchObject({ seenVersion: 2, takeoverVersion: 2 });
  });

  it('queues a voice callout for a new shot only when voice is on', () => {
    expect(nextShotCue(idle, input({ voiceCallout: true, bigNumberAfterShot: false }))).toEqual({
      seenVersion: 1,
      takeoverVersion: null,
      callout: '214 yards',
    });
    expect(nextShotCue(idle, input({ voiceCallout: false })).callout).toBeNull();
    expect(nextShotCue(idle, input({ voiceCallout: true, isNewShot: false })).callout).toBeNull();
    expect(nextShotCue(idle, input({ voiceCallout: true, metric: { ...carry, value: '—' } })).callout).toBeNull();
  });

  it('speaks on any kiosk view, not just Live', () => {
    expect(nextShotCue(idle, input({ voiceCallout: true, liveView: false })).callout).toBe('214 yards');
  });
});

describe('hasCalloutValue', () => {
  it('rejects the no-value placeholder', () => {
    expect(hasCalloutValue(carry)).toBe(true);
    expect(hasCalloutValue({ ...carry, value: '—' })).toBe(false);
    expect(hasCalloutValue(null)).toBe(false);
  });
});

describe('calloutText', () => {
  it('speaks the value with the unit spelled out', () => {
    expect(calloutText(carry)).toBe('214 yards');
    expect(calloutText({ id: 'ball_speed', label: 'Ball speed', value: '148.1', unit: 'km/h' })).toBe(
      '148.1 kilometers per hour'
    );
    expect(calloutText({ id: 'launch_v', label: 'V. launch', value: '13.4', unit: '°' })).toBe('13.4 degrees');
  });

  it('leaves a unitless value bare and an unknown unit as is', () => {
    expect(calloutText({ id: 'smash', label: 'Smash', value: '1.45' })).toBe('1.45');
    expect(calloutText({ id: 'x', label: 'X', value: '3', unit: 'g' })).toBe('3 g');
  });

  it('speaks a grouped spin value as is, never as a decimal', () => {
    const spin = { id: 'spin', label: 'Spin', value: (2328).toLocaleString('en-US'), unit: 'rpm' };
    expect(spin.value).toBe('2,328');
    expect(calloutText(spin)).toBe('2,328 rpm');

    const wedgeSpin = { id: 'spin', label: 'Spin', value: (10250).toLocaleString('en-US'), unit: 'rpm' };
    expect(calloutText(wedgeSpin)).toBe('10,250 rpm');
    expect(calloutText({ id: 'smash', label: 'Smash', value: '1.48' })).toBe('1.48');
  });

  it('has nothing to say without a value', () => {
    expect(calloutText({ ...carry, value: '—' })).toBeNull();
    expect(calloutText(null)).toBeNull();
  });
});
