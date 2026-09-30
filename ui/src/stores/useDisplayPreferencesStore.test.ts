import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.display-preferences';

const ALL_OFF = {
  bigNumberAfterShot: false,
  consistencyColors: false,
  voiceCallout: false,
  showNormalizedCarry: false,
};

function installBrowser(
  initial: Record<string, string> = {},
  options: { failReads?: boolean; failWrites?: boolean } = {}
) {
  const store = { ...initial };
  const localStorage = {
    getItem: (key: string) => {
      if (options.failReads) throw new Error('SecurityError');
      return store[key] ?? null;
    },
    setItem: (key: string, value: string) => {
      if (options.failWrites) throw new Error('quota exceeded');
      store[key] = value;
    },
  };
  vi.stubGlobal('window', { localStorage });
  return store;
}

async function loadStore() {
  return (await import('./useDisplayPreferencesStore')).useDisplayPreferencesStore;
}

describe('useDisplayPreferencesStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('starts with every display extra off', async () => {
    installBrowser();
    const store = await loadStore();

    expect(store.getState().preferences).toEqual(ALL_OFF);
  });

  it('restores stored choices', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ voiceCallout: true, consistencyColors: true }) });
    const store = await loadStore();

    expect(store.getState().preferences).toEqual({ ...ALL_OFF, voiceCallout: true, consistencyColors: true });
  });

  it('ignores values that are not exactly true', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ voiceCallout: 'yes', bigNumberAfterShot: 1, other: true }) });
    const store = await loadStore();

    expect(store.getState().preferences).toEqual(ALL_OFF);
  });

  it('falls back to all off for corrupt JSON or unreadable storage', async () => {
    installBrowser({ [STORAGE_KEY]: '{not json' });
    expect((await loadStore()).getState().preferences).toEqual(ALL_OFF);

    vi.resetModules();
    installBrowser({}, { failReads: true });
    expect((await loadStore()).getState().preferences).toEqual(ALL_OFF);
  });

  it('falls back to all off when there is no window', async () => {
    vi.stubGlobal('window', undefined);
    const store = await loadStore();

    expect(store.getState().preferences).toEqual(ALL_OFF);
  });

  it('persists a change without touching the other choices', async () => {
    const storage = installBrowser({ [STORAGE_KEY]: JSON.stringify({ voiceCallout: true }) });
    const store = await loadStore();

    store.getState().setPreference('showNormalizedCarry', true);

    expect(store.getState().preferences).toEqual({ ...ALL_OFF, voiceCallout: true, showNormalizedCarry: true });
    expect(JSON.parse(storage[STORAGE_KEY])).toEqual({ ...ALL_OFF, voiceCallout: true, showNormalizedCarry: true });
  });

  it('still applies the choice when writing storage throws', async () => {
    installBrowser({}, { failWrites: true });
    const store = await loadStore();

    expect(() => store.getState().setPreference('bigNumberAfterShot', true)).not.toThrow();
    expect(store.getState().preferences.bigNumberAfterShot).toBe(true);
  });
});
