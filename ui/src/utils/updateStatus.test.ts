import { describe, expect, it } from 'vitest';
import { t } from '../i18n';
import type { UpdateStatus } from '../types/socket';
import {
  canCheckForUpdates,
  canInstallUpdate,
  isUpdateInProgress,
  shouldReloadAfterReconnect,
  updateDetail,
  updateLabel,
  updateStepLabel,
} from './updateStatus';

const base: UpdateStatus = { enabled: true, state: 'up_to_date', current: 'abc1234', can_apply: true };

describe('update status helpers', () => {
  it('does nothing when updates are off', () => {
    const off: UpdateStatus = { enabled: false, state: 'disabled' };
    expect(isUpdateInProgress(off)).toBe(false);
    expect(shouldReloadAfterReconnect(off)).toBe(false);
    expect(canCheckForUpdates(off)).toBe(false);
    expect(canInstallUpdate(off)).toBe(false);
    expect(isUpdateInProgress(null)).toBe(false);
  });

  it('only lets the kiosk act', () => {
    expect(canCheckForUpdates(base)).toBe(true);
    expect(canCheckForUpdates({ ...base, can_apply: false })).toBe(false);
    expect(canInstallUpdate({ ...base, state: 'available' })).toBe(true);
    expect(canInstallUpdate({ ...base, state: 'available', can_apply: false })).toBe(false);
    expect(canCheckForUpdates({ ...base, state: 'available' })).toBe(false);
    expect(canCheckForUpdates({ ...base, state: 'updating' })).toBe(false);
  });

  it('reloads after a restart, including after a rolled-back failure', () => {
    expect(shouldReloadAfterReconnect({ ...base, state: 'restarting' })).toBe(true);
    expect(shouldReloadAfterReconnect({ ...base, state: 'failed' })).toBe(true);
    expect(shouldReloadAfterReconnect({ ...base, state: 'available' })).toBe(false);
    expect(isUpdateInProgress({ ...base, state: 'updating' })).toBe(true);
  });

  it('labels each state', () => {
    expect(updateLabel(base, t)).toBe('Up to date · abc1234');
    expect(updateLabel({ ...base, state: 'available' }, t)).toBe('Update available');
    expect(updateLabel({ ...base, state: 'idle', current: null }, t)).toBe('Not checked yet');
    expect(updateLabel({ ...base, state: 'idle' }, t)).toBe('Version abc1234');
    expect(updateLabel({ ...base, state: 'error' }, t)).toBe("Can't check for updates");
  });

  it('adds the useful second line', () => {
    expect(updateDetail({ ...base, state: 'available', behind: 3 }, t)).toBe('Changes: 3');
    expect(updateDetail({ ...base, state: 'error', error: 'The Pi is on dev, not main' }, t)).toBe(
      'The Pi is on dev, not main'
    );
    const installed = {
      ok: true,
      previous: 'a',
      installed: 'b1b1b1b',
      error: null,
      rolled_back: false,
      finished_at: '',
      log_path: null,
    };
    expect(updateDetail({ ...base, last_result: installed }, t)).toBe('Updated to b1b1b1b');
    expect(updateDetail({ ...base, last_result: { ...installed, ok: false } }, t)).toContain('previous version');
    expect(updateDetail(base, t)).toBeNull();
  });

  it('translates known steps and passes unknown ones through', () => {
    expect(updateStepLabel('Rolling back', t)).toBe('Rolling back');
    expect(updateStepLabel('Something new', t)).toBe('Something new');
    expect(updateStepLabel(null, t)).toBeUndefined();
  });
});
