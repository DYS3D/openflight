import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setActiveLocale } from '../i18n';
import type { SimNotice } from '../stores/useBannerStore';
import { StatusBanner } from './StatusBanner';

const bannerState = vi.hoisted(() => ({
  notice: null as SimNotice | null,
  reconnectAttempt: null as number | null,
  dismissNotice: () => {},
}));

vi.mock('../stores/useBannerStore', () => ({
  useBannerStore: <T,>(selector: (state: typeof bannerState) => T) => selector(bannerState),
}));

describe('StatusBanner', () => {
  beforeEach(() => {
    bannerState.notice = null;
    bannerState.reconnectAttempt = null;
  });

  afterEach(() => {
    setActiveLocale('en');
  });

  it('renders nothing when there is nothing to report', () => {
    expect(renderToString(<StatusBanner />)).toBe('');
  });

  it('shows the reconnect attempt count', () => {
    bannerState.reconnectAttempt = 3;
    const html = renderToString(<StatusBanner />);

    expect(html).toContain('role="status"');
    expect(html).toContain('Reconnecting (3)…');
  });

  it('shows a failed simulator send with a dismiss button', () => {
    bannerState.notice = { id: 1, kind: 'simSendFailed', target: 'GSPro', reason: 'connection refused' };
    const html = renderToString(<StatusBanner />);

    expect(html).toContain('role="alert"');
    expect(html).toContain('Could not send shot to GSPro: connection refused');
    expect(html).toContain('aria-label="Dismiss notice"');
    expect(html).toContain('status-banner__dismiss');
  });

  it('shows a dropped simulator shot', () => {
    bannerState.notice = { id: 2, kind: 'simShotDropped', reason: 'simulator busy' };

    expect(renderToString(<StatusBanner />)).toContain('Shot not sent to simulator: simulator busy');
  });

  it('shows both banners together while reconnecting', () => {
    bannerState.reconnectAttempt = 1;
    bannerState.notice = { id: 3, kind: 'simShotDropped', reason: 'offline' };
    const html = renderToString(<StatusBanner />);

    expect(html).toContain('Reconnecting (1)…');
    expect(html).toContain('Shot not sent to simulator: offline');
  });

  it('translates the banner text', () => {
    setActiveLocale('es');
    bannerState.reconnectAttempt = 2;

    expect(renderToString(<StatusBanner />)).toContain('Reconectando (2)…');
  });
});
