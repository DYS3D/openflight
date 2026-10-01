import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_DISPLAY_PREFERENCES } from '../../stores/useDisplayPreferencesStore';
import { DisplayPreferencesSection } from './DisplayPreferencesSection';
import { MenuSheet } from './MenuSheet';

function switches(html: string): Array<{ label: string; checked: string }> {
  return [
    ...html.matchAll(/role="switch" aria-checked="(true|false)"[^>]*>.*?menu-sheet__switch-label">([^<]+)</g),
  ].map((match) => ({ label: match[2], checked: match[1] }));
}

describe('DisplayPreferencesSection', () => {
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
      { label: 'More metrics', checked: 'false' },
    ]);
  });

  it('marks switched-on extras as checked', () => {
    const html = renderToString(
      <DisplayPreferencesSection
        preferences={{ ...DEFAULT_DISPLAY_PREFERENCES, voiceCallout: true, showNormalizedCarry: true }}
        onChange={() => {}}
      />
    );

    expect(switches(html).map((item) => item.checked)).toEqual(['false', 'false', 'true', 'true', 'false']);
  });

  describe('no-voices note', () => {
    const NOTE = 'No voices installed on this device';

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    function stubVoices(count: number) {
      vi.stubGlobal('window', {
        speechSynthesis: { getVoices: () => Array.from({ length: count }, () => ({})) },
      });
    }

    function render(voiceCallout: boolean) {
      return renderToString(
        <DisplayPreferencesSection preferences={{ ...DEFAULT_DISPLAY_PREFERENCES, voiceCallout }} onChange={() => {}} />
      );
    }

    it('shows when voice callout is on and the device has no voices', () => {
      stubVoices(0);

      expect(render(true)).toContain(NOTE);
    });

    it('stays hidden when voice callout is off', () => {
      stubVoices(0);

      expect(render(false)).not.toContain(NOTE);
    });

    it('stays hidden when a voice is installed', () => {
      stubVoices(1);

      expect(render(true)).not.toContain(NOTE);
    });
  });

  it('appears in the menu sheet with everything off', () => {
    const html = renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />);

    expect(html).toContain('menu-sheet__section-title">Display<');
    expect(switches(html)).toHaveLength(5);
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
