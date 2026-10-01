import { renderToString } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SimNotice } from '../stores/useBannerStore';
import type { LevelStatus, RadarHealth } from '../types/socket';
import { StatusBanner } from './StatusBanner';

const bannerState = vi.hoisted(() => ({
  notice: null as SimNotice | null,
  reconnectAttempt: null as number | null,
  levelWarning: null as LevelStatus | null,
  radarHealth: null as RadarHealth | null,
  dismissNotice: () => {},
}));

vi.mock('../stores/useBannerStore', () => ({
  useBannerStore: <T,>(selector: (state: typeof bannerState) => T) => selector(bannerState),
}));

describe('StatusBanner', () => {
  beforeEach(() => {
    bannerState.notice = null;
    bannerState.reconnectAttempt = null;
    bannerState.levelWarning = null;
    bannerState.radarHealth = null;
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

  it('shows a refused shot delete', () => {
    bannerState.notice = { id: 4, kind: 'deleteShotFailed', reason: 'Shot not found' };

    expect(renderToString(<StatusBanner />)).toContain('Could not delete shot: Shot not found');
  });

  it('shows both banners together while reconnecting', () => {
    bannerState.reconnectAttempt = 1;
    bannerState.notice = { id: 3, kind: 'simShotDropped', reason: 'offline' };
    const html = renderToString(<StatusBanner />);

    expect(html).toContain('Reconnecting (1)…');
    expect(html).toContain('Shot not sent to simulator: offline');
  });

  it('shows pitch and roll while the unit is not level', () => {
    bannerState.levelWarning = { pitch_deg: 2.44, roll_deg: -0.6, level: false, threshold_deg: 1.5 };
    const html = renderToString(<StatusBanner />).replace(/<!-- -->/g, '');

    expect(html).toContain('status-banner--level');
    expect(html).toContain('role="status"');
    expect(html).toContain('Unit is not level (pitch 2.4°, roll -0.6°)');
    expect(html).not.toContain('status-banner__dismiss');
  });

  it('stays silent for radar health without interference', () => {
    bannerState.radarHealth = {
      interference: false,
      noise_floor_db: -61,
      baseline_db: -62,
      updated_at: '2026-09-30T10:00:00Z',
    };

    expect(renderToString(<StatusBanner />)).toBe('');
  });

  it('announces radar interference with the noise rise over baseline', () => {
    bannerState.radarHealth = {
      interference: true,
      noise_floor_db: -58.5,
      baseline_db: -62,
      updated_at: '2026-09-30T10:00:00Z',
    };
    const html = renderToString(<StatusBanner />);

    expect(html).toContain('status-banner--interference');
    expect(html).toContain('role="status"');
    expect(html).toContain('Radar interference detected (noise +3.5 dB)');
  });
});
