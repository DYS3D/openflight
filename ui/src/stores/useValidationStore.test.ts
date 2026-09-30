import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight-validation-entries';

interface StorageOptions {
  failReads?: boolean;
  failWrites?: boolean;
}

function installBrowser(initial: Record<string, string> = {}, options: StorageOptions = {}) {
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
  return (await import('./useValidationStore')).useValidationStore;
}

const entry = (notes = '') => ({ comparatorDevice: 'Mevo', comparatorSpeed: '150', notes });

function isoAt(index: number) {
  return new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString();
}

describe('useValidationStore persistence', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads valid stored entries', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ [isoAt(1)]: entry('ok') }) });
    const store = await loadStore();

    expect(store.getState().entries).toEqual({ [isoAt(1)]: entry('ok') });
  });

  it('starts empty when the stored JSON is corrupt', async () => {
    installBrowser({ [STORAGE_KEY]: '{not json' });
    const store = await loadStore();

    expect(store.getState().entries).toEqual({});
  });

  it.each([
    ['an array', '[1, 2]'],
    ['a string', '"hello"'],
    ['a number', '42'],
    ['null', 'null'],
  ])('starts empty when the stored JSON is %s', async (_label, raw) => {
    installBrowser({ [STORAGE_KEY]: raw });
    const store = await loadStore();

    expect(store.getState().entries).toEqual({});
  });

  it('drops entries with the wrong shape and keeps the valid ones', async () => {
    installBrowser({
      [STORAGE_KEY]: JSON.stringify({
        [isoAt(1)]: entry('keep'),
        [isoAt(2)]: null,
        [isoAt(3)]: 'text',
        [isoAt(4)]: { comparatorDevice: 'Mevo', comparatorSpeed: 150, notes: '' },
        [isoAt(5)]: { comparatorDevice: 'Mevo', notes: '' },
        [isoAt(6)]: { ...entry('extra'), injected: '<script>' },
      }),
    });
    const store = await loadStore();

    expect(store.getState().entries).toEqual({
      [isoAt(1)]: entry('keep'),
      [isoAt(6)]: entry('extra'),
    });
  });

  it('keeps only the newest entries when storage holds more than the cap', async () => {
    const { MAX_VALIDATION_ENTRIES } = await import('./useValidationStore');
    vi.resetModules();
    const stored: Record<string, ReturnType<typeof entry>> = {};
    for (let i = 0; i < MAX_VALIDATION_ENTRIES + 20; i += 1) {
      stored[isoAt(i)] = entry(String(i));
    }
    installBrowser({ [STORAGE_KEY]: JSON.stringify(stored) });
    const store = await loadStore();

    const kept = Object.keys(store.getState().entries);
    expect(kept).toHaveLength(MAX_VALIDATION_ENTRIES);
    expect(kept).not.toContain(isoAt(0));
    expect(kept).not.toContain(isoAt(19));
    expect(kept).toContain(isoAt(20));
    expect(kept).toContain(isoAt(MAX_VALIDATION_ENTRIES + 19));
  });

  it('evicts the oldest entry when an update goes past the cap', async () => {
    const { MAX_VALIDATION_ENTRIES } = await import('./useValidationStore');
    vi.resetModules();
    const stored: Record<string, ReturnType<typeof entry>> = {};
    for (let i = 0; i < MAX_VALIDATION_ENTRIES; i += 1) {
      stored[isoAt(i)] = entry(String(i));
    }
    const storage = installBrowser({ [STORAGE_KEY]: JSON.stringify(stored) });
    const store = await loadStore();

    store.getState().updateEntry(isoAt(MAX_VALIDATION_ENTRIES), { notes: 'new' });

    const entries = store.getState().entries;
    expect(Object.keys(entries)).toHaveLength(MAX_VALIDATION_ENTRIES);
    expect(entries[isoAt(0)]).toBeUndefined();
    expect(entries[isoAt(MAX_VALIDATION_ENTRIES)].notes).toBe('new');
    expect(Object.keys(JSON.parse(storage[STORAGE_KEY]))).toHaveLength(MAX_VALIDATION_ENTRIES);
  });

  it('starts empty when reading storage throws', async () => {
    installBrowser({}, { failReads: true });
    const store = await loadStore();

    expect(store.getState().entries).toEqual({});
  });

  it('still applies updates, removals and clears when writing storage throws', async () => {
    installBrowser({}, { failWrites: true });
    const store = await loadStore();

    expect(() => store.getState().updateEntry(isoAt(1), { notes: 'kept in memory' })).not.toThrow();
    expect(store.getState().entries[isoAt(1)].notes).toBe('kept in memory');

    expect(() => store.getState().removeEntry(isoAt(1))).not.toThrow();
    expect(store.getState().entries).toEqual({});

    expect(() => store.getState().clearEntries()).not.toThrow();
  });

  it('persists updates', async () => {
    const storage = installBrowser();
    const store = await loadStore();

    store.getState().updateEntry(isoAt(1), { comparatorSpeed: '151' });

    expect(JSON.parse(storage[STORAGE_KEY])).toEqual({
      [isoAt(1)]: { comparatorDevice: '', comparatorSpeed: '151', notes: '' },
    });
  });
});
