export const PREVIEW_REFRESH_MS = 5000;
/** The capture runtime can start after the page opens, so a missing preview is retried, just less often. */
export const PREVIEW_UNAVAILABLE_RETRY_MS = 30_000;

/**
 * Run `refresh` now and again once each run settles, so slow responses never
 * overlap. `refresh` resolves false while the preview is unavailable.
 */
export function startPreviewPolling(refresh: () => Promise<boolean>): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    const available = await refresh();
    if (stopped) return;
    timer = setTimeout(tick, available ? PREVIEW_REFRESH_MS : PREVIEW_UNAVAILABLE_RETRY_MS);
  };

  void tick();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
