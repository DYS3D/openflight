import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { MenuSheet } from './MenuSheet';
import { useSystemStore } from '../../stores/useSystemStore';
import type { PowerStatus } from '../../types/power';
import type { UpdateStatus } from '../../types/socket';

function renderMenu() {
  return renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />);
}

describe('MenuSheet profiles', () => {
  it('does not manage profiles in the menu', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">Profile');
    expect(html).not.toContain('Add profile');
    expect(html).not.toContain('menu-sheet__input');
  });
});

describe('MenuSheet language', () => {
  it('offers a language dropdown with the shipped locales', () => {
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Language');
    expect(html).toContain('aria-label="Language"');
    expect(html).toContain('>English</option>');
    expect(html).toContain('>Español</option>');
    expect(html).toContain('>Français</option>');
    expect(html).toContain('>Português</option>');
  });
});

describe('MenuSheet battery', () => {
  it('does not show battery in the menu, even when telemetry is present', () => {
    const powerStatus: PowerStatus = {
      available: true,
      provider: 'geekworm',
      state: 'on_battery',
      battery_percent: 64.2,
      battery_voltage_v: 3.81,
      external_power: false,
      updated_at: '2026-08-20T12:00:00+00:00',
      error: null,
    };
    useSystemStore.setState({ powerStatus });

    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__status-label">Battery');
    expect(html).not.toContain('power-status');
    expect(html).not.toContain('64%');

    useSystemStore.setState({ powerStatus: null });
  });
});

describe('MenuSheet software updates', () => {
  const status: UpdateStatus = { enabled: true, state: 'available', current: 'aaaaaaa', behind: 2, can_apply: true };

  function renderWith(updateStatus: UpdateStatus | null) {
    return renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} updateStatus={updateStatus} />);
  }

  it('shows nothing when updates are off', () => {
    expect(renderWith(null)).not.toContain('Software');
    expect(renderWith({ enabled: false, state: 'disabled' })).not.toContain('Software');
  });

  it('offers Update on the kiosk when one is available', () => {
    const html = renderWith(status);
    expect(html).toContain('menu-sheet__status-label">Software');
    expect(html).toContain('Update available');
    expect(html).toContain('Changes: 2');
    expect(html).toContain('menu-sheet__update-button">Update</button>');
  });

  it('shows a phone the status without buttons', () => {
    const html = renderWith({ ...status, can_apply: false });
    expect(html).toContain('Update available');
    expect(html).not.toContain('menu-sheet__update-button');
  });

  it('offers Check now when up to date', () => {
    const html = renderWith({ ...status, state: 'up_to_date', behind: 0 });
    expect(html).toContain('Up to date · aaaaaaa');
    expect(html).toContain('>Check now</button>');
  });
});
