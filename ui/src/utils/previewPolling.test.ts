import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PREVIEW_REFRESH_MS, PREVIEW_UNAVAILABLE_RETRY_MS, startPreviewPolling } from './previewPolling';

describe('startPreviewPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refreshes immediately and then every few seconds while available', async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const stop = startPreviewPolling(refresh);
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(PREVIEW_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(2);
    stop();
  });

  it('keeps checking, more slowly, after the preview is missing', async () => {
    const refresh = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    const stop = startPreviewPolling(refresh);
    await vi.advanceTimersByTimeAsync(PREVIEW_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(PREVIEW_UNAVAILABLE_RETRY_MS - PREVIEW_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(PREVIEW_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(3);
    stop();
  });

  it('never starts a refresh while the previous one is still running', async () => {
    let finish: (available: boolean) => void = () => {};
    const refresh = vi.fn(() => new Promise<boolean>((resolve) => (finish = resolve)));
    const stop = startPreviewPolling(refresh);

    await vi.advanceTimersByTimeAsync(PREVIEW_REFRESH_MS * 4);
    expect(refresh).toHaveBeenCalledTimes(1);

    finish(true);
    await vi.advanceTimersByTimeAsync(PREVIEW_REFRESH_MS);
    expect(refresh).toHaveBeenCalledTimes(2);
    stop();
  });

  it('stops after cleanup', async () => {
    const refresh = vi.fn().mockResolvedValue(true);
    const stop = startPreviewPolling(refresh);
    await vi.advanceTimersByTimeAsync(0);
    stop();

    await vi.advanceTimersByTimeAsync(PREVIEW_UNAVAILABLE_RETRY_MS);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
