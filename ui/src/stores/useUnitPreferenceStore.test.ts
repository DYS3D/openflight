import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.unit-system';

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
  return (await import('./useUnitPreferenceStore')).useUnitPreferenceStore;
}

describe('useUnitPreferenceStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('restores a stored metric preference', async () => {
    installBrowser({ [STORAGE_KEY]: 'metric' });
    const store = await loadStore();

    expect(store.getState().unitSystem).toBe('metric');
  });

  it('falls back to imperial for unknown stored values', async () => {
    installBrowser({ [STORAGE_KEY]: 'furlongs' });
    const store = await loadStore();

    expect(store.getState().unitSystem).toBe('imperial');
  });

  it('falls back to imperial when reading storage throws', async () => {
    installBrowser({}, { failReads: true });
    const store = await loadStore();

    expect(store.getState().unitSystem).toBe('imperial');
  });

  it('persists a new choice', async () => {
    const storage = installBrowser();
    const store = await loadStore();

    store.getState().setUnitSystem('metric');

    expect(storage[STORAGE_KEY]).toBe('metric');
  });

  it('still applies the choice when writing storage throws', async () => {
    installBrowser({}, { failWrites: true });
    const store = await loadStore();

    expect(() => store.getState().setUnitSystem('metric')).not.toThrow();
    expect(store.getState().unitSystem).toBe('metric');
  });
});
