// A kiosk has no address bar or reload key, so a crashed renderer or a failed
// load would leave a blank screen until someone power-cycles the Pi.

export const RECOVERY_DELAY_MS = 2000;
export const MAX_RECOVERY_DELAY_MS = 30000;
const ERR_ABORTED = -3;

/** Reload `targetUrl` after a renderer crash or failed main-frame load, backing off while it keeps failing. */
export function attachRecovery(webContents, targetUrl, schedule = setTimeout) {
  let delay = RECOVERY_DELAY_MS;
  let pending = false;

  const recover = () => {
    if (pending) return;
    pending = true;
    schedule(() => {
      pending = false;
      if (!webContents.isDestroyed()) webContents.loadURL(targetUrl);
    }, delay);
    delay = Math.min(delay * 2, MAX_RECOVERY_DELAY_MS);
  };

  webContents.on('render-process-gone', recover);
  webContents.on('did-fail-load', (_event, errorCode, _description, _url, isMainFrame) => {
    if (isMainFrame && errorCode !== ERR_ABORTED) recover();
  });
  webContents.on('did-finish-load', () => {
    delay = RECOVERY_DELAY_MS;
  });
}
