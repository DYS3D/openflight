import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NOTICE_TIMEOUT_MS, useBannerStore } from './useBannerStore';

describe('useBannerStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useBannerStore.getState().dismissNotice();
    useBannerStore.getState().clearReconnect();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows a notice and auto-dismisses it after the timeout', () => {
    useBannerStore.getState().showNotice({ kind: 'simShotDropped', reason: 'no simulator' });

    expect(useBannerStore.getState().notice).toMatchObject({ kind: 'simShotDropped', reason: 'no simulator' });

    vi.advanceTimersByTime(NOTICE_TIMEOUT_MS - 1);
    expect(useBannerStore.getState().notice).not.toBeNull();

    vi.advanceTimersByTime(1);
    expect(useBannerStore.getState().notice).toBeNull();
  });

  it('dismisses immediately on request and cancels the pending timer', () => {
    useBannerStore.getState().showNotice({ kind: 'simShotDropped', reason: 'first' });
    useBannerStore.getState().dismissNotice();

    expect(useBannerStore.getState().notice).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('restarts the timeout when a newer notice replaces the current one', () => {
    useBannerStore.getState().showNotice({ kind: 'simShotDropped', reason: 'first' });
    const firstId = useBannerStore.getState().notice?.id;
    vi.advanceTimersByTime(NOTICE_TIMEOUT_MS - 1000);

    useBannerStore.getState().showNotice({ kind: 'simSendFailed', target: 'gspro', reason: 'refused' });
    const second = useBannerStore.getState().notice;
    expect(second?.id).not.toBe(firstId);

    vi.advanceTimersByTime(1000);
    expect(useBannerStore.getState().notice).toEqual(second);

    vi.advanceTimersByTime(NOTICE_TIMEOUT_MS - 1000);
    expect(useBannerStore.getState().notice).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('tracks and clears the reconnect attempt', () => {
    useBannerStore.getState().setReconnectAttempt(3);
    expect(useBannerStore.getState().reconnectAttempt).toBe(3);

    useBannerStore.getState().clearReconnect();
    expect(useBannerStore.getState().reconnectAttempt).toBeNull();
  });
});
