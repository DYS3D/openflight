import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOCALE_STORAGE_KEY } from '../i18n';

function installBrowser(initial: Record<string, string> = {}, options: { failWrites?: boolean } = {}) {
  const store = { ...initial };
  const documentElement = { lang: '' };
  const localStorage = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      if (options.failWrites) {
        throw new Error('quota exceeded');
      }
      store[key] = value;
    },
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('document', { documentElement });
  vi.stubGlobal('window', { localStorage });
  return { documentElement, store };
}

describe('useLocaleStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to English and sets <html lang>', async () => {
    const { documentElement } = installBrowser();
    const { useLocaleStore } = await import('./useLocaleStore');
    const { t } = await import('../i18n');

    expect(useLocaleStore.getState().locale).toBe('en');
    expect(documentElement.lang).toBe('en');
    expect(t('nav.live')).toBe('Live');
  });

  it('falls back to English when storage holds a locale that is no longer shipped', async () => {
    const { documentElement } = installBrowser({ [LOCALE_STORAGE_KEY]: 'es' });
    const { useLocaleStore } = await import('./useLocaleStore');
    const { t } = await import('../i18n');

    expect(useLocaleStore.getState().locale).toBe('en');
    expect(documentElement.lang).toBe('en');
    expect(t('nav.live')).toBe('Live');
  });

  it('persists the chosen locale', async () => {
    const { store } = installBrowser();
    const { useLocaleStore } = await import('./useLocaleStore');

    useLocaleStore.getState().setLocale('en');

    expect(store[LOCALE_STORAGE_KEY]).toBe('en');
  });

  it('applies the locale for the session when storage rejects the write', async () => {
    const { documentElement } = installBrowser({}, { failWrites: true });
    const { useLocaleStore } = await import('./useLocaleStore');

    expect(() => useLocaleStore.getState().setLocale('en')).not.toThrow();
    expect(useLocaleStore.getState().locale).toBe('en');
    expect(documentElement.lang).toBe('en');
  });
});
