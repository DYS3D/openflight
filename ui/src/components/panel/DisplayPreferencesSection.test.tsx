import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale } from '../../i18n';
import { DEFAULT_DISPLAY_PREFERENCES } from '../../stores/useDisplayPreferencesStore';
import { DisplayPreferencesSection } from './DisplayPreferencesSection';
import { MenuSheet } from './MenuSheet';

function switches(html: string): Array<{ label: string; checked: string }> {
  return [
    ...html.matchAll(/role="switch" aria-checked="(true|false)"[^>]*>.*?menu-sheet__switch-label">([^<]+)</g),
  ].map((match) => ({ label: match[2], checked: match[1] }));
}

describe('DisplayPreferencesSection', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('lists every display extra as an unchecked switch by default', () => {
    const html = renderToString(
      <DisplayPreferencesSection preferences={DEFAULT_DISPLAY_PREFERENCES} onChange={() => {}} />
    );

    expect(html).toContain('menu-sheet__section-title">Display<');
    expect(switches(html)).toEqual([
      { label: 'Big number after shot', checked: 'false' },
      { label: 'Consistency colours', checked: 'false' },
      { label: 'Voice callout', checked: 'false' },
      { label: 'Show normalized carry', checked: 'false' },
    ]);
  });

  it('marks switched-on extras as checked', () => {
    const html = renderToString(
      <DisplayPreferencesSection
        preferences={{ ...DEFAULT_DISPLAY_PREFERENCES, voiceCallout: true, showNormalizedCarry: true }}
        onChange={() => {}}
      />
    );

    expect(switches(html).map((item) => item.checked)).toEqual(['false', 'false', 'true', 'true']);
  });

  it('translates the labels', () => {
    setActiveLocale('fr');
    const html = renderToString(
      <DisplayPreferencesSection preferences={DEFAULT_DISPLAY_PREFERENCES} onChange={() => {}} />
    );

    expect(html).toContain('>Affichage<');
    expect(html).toContain('>Annonce vocale<');
  });

  it('appears in the menu sheet with everything off', () => {
    const html = renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />);

    expect(html).toContain('menu-sheet__section-title">Display<');
    expect(switches(html)).toHaveLength(4);
    expect(html).not.toContain('aria-checked="true"');
  });

  it('sits after System and pairs its switches across the short-kiosk sheet', () => {
    const html = renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />);
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');
    const shortKiosk = css.slice(css.indexOf('@media (max-height: 500px)'));

    expect(html.indexOf('>System<')).toBeLessThan(html.indexOf('>Display<'));
    expect(html.indexOf('>Display<')).toBeLessThan(html.indexOf('menu-sheet__shutdown'));
    expect(shortKiosk).toMatch(
      /\.menu-sheet__section--display \{[^}]*grid-column: 1 \/ -1;[^}]*grid-template-columns: 1fr 1fr/
    );
  });
});
