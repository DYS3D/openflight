import { afterEach, describe, expect, it } from 'vitest';
import { catalogs, LOCALES, setActiveLocale, t } from './index';
import { en } from './en';

describe('i18n catalogs', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('ships English only', () => {
    expect(LOCALES.map((locale) => locale.id)).toEqual(['en']);
    expect(Object.keys(catalogs)).toEqual(['en']);
  });

  it('keeps every catalog in lockstep with English keys', () => {
    const englishKeys = Object.keys(en).sort();
    expect(englishKeys.length).toBeGreaterThan(80);

    for (const [id, messages] of Object.entries(catalogs)) {
      expect(Object.keys(messages).sort(), id).toEqual(englishKeys);
    }
  });

  it('interpolates placeholders and leaves unknown ones visible', () => {
    expect(t('profiles.shots', { count: '3' })).toBe('3 shots');
    expect(t('profiles.shots', {})).toBe('{count} shots');
  });

  it('falls back to English when a locale id is unknown', () => {
    expect(setActiveLocale('de')).toBe('en');
    expect(t('nav.live')).toBe('Live');
  });
});
