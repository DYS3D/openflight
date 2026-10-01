import { beforeEach, describe, expect, it } from 'vitest';
import { makeTestSession } from '../test/shotFixtures';
import { defaultPracticeConfig } from '../utils/practice';
import { startPracticeSession, usePracticeStore } from './usePracticeStore';

describe('usePracticeStore', () => {
  beforeEach(() => {
    usePracticeStore.setState({ sessions: {}, lastConfig: null });
  });

  it('starts with no rounds', () => {
    expect(usePracticeStore.getState().sessions).toEqual({});
    expect(usePracticeStore.getState().lastConfig).toBeNull();
  });

  it('keeps a separate round per golfer', () => {
    const james = startPracticeSession(defaultPracticeConfig('imperial', 1), []);
    const alex = startPracticeSession({ ...defaultPracticeConfig('imperial', 2), mode: 'ladder' }, []);

    usePracticeStore.getState().setSession('james', james);
    usePracticeStore.getState().setSession('alex', alex);

    expect(usePracticeStore.getState().sessions).toEqual({ james, alex });
    expect(usePracticeStore.getState().lastConfig).toBe(alex.config);
  });

  it('replaces only that golfer’s round on restart', () => {
    const james = startPracticeSession(defaultPracticeConfig('imperial', 1), []);
    const alex = startPracticeSession(defaultPracticeConfig('imperial', 2), []);
    const restarted = startPracticeSession(defaultPracticeConfig('imperial', 3), []);
    usePracticeStore.getState().setSession('james', james);
    usePracticeStore.getState().setSession('alex', alex);

    usePracticeStore.getState().setSession('james', restarted);

    expect(usePracticeStore.getState().sessions.james).toBe(restarted);
    expect(usePracticeStore.getState().sessions.alex).toBe(alex);
  });
});

describe('startPracticeSession', () => {
  it('baselines every shot already in the session so they are not scored', () => {
    const shots = makeTestSession('7-iron', [150, 151]);

    const session = startPracticeSession(defaultPracticeConfig('metric', 7), shots);

    expect(session.config.seed).toBe(7);
    expect([...session.baseline]).toEqual(shots.map((shot) => shot.timestamp));
  });
});
