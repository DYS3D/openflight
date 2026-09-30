import type { MessageKey } from '../i18n';
import type { UpdateStatus } from '../types/socket';

type Translate = (key: MessageKey, params?: Record<string, string>) => string;

/** States in which the server is busy installing, or about to restart. */
export function isUpdateInProgress(status: UpdateStatus | null | undefined): boolean {
  return Boolean(status?.enabled && ['updating', 'restarting', 'failed'].includes(status.state));
}

/**
 * After an update the server exits and systemd starts the new version. Once
 * the socket is back, reload so the browser picks up the new UI bundle.
 */
export function shouldReloadAfterReconnect(status: UpdateStatus | null | undefined): boolean {
  return Boolean(status?.enabled && (status.state === 'restarting' || status.state === 'failed'));
}

/** Whether "Check now" makes sense (not while busy, not when an update is waiting). */
export function canCheckForUpdates(status: UpdateStatus | null | undefined): boolean {
  return Boolean(status?.enabled && status.can_apply && ['idle', 'up_to_date', 'error'].includes(status.state));
}

export function canInstallUpdate(status: UpdateStatus | null | undefined): boolean {
  return Boolean(status?.enabled && status.can_apply && status.state === 'available');
}

/** One-line state for the menu's Software row. */
export function updateLabel(status: UpdateStatus, t: Translate): string {
  switch (status.state) {
    case 'checking':
      return t('update.checking');
    case 'up_to_date':
      return status.current ? `${t('update.upToDate')} · ${status.current}` : t('update.upToDate');
    case 'available':
      return t('update.available');
    case 'updating':
      return t('update.updating');
    case 'restarting':
      return t('update.restarting');
    case 'failed':
      return t('update.failed');
    case 'error':
      return t('update.unavailable');
    default:
      return status.current ? t('update.version', { version: status.current }) : t('update.notChecked');
  }
}

/** Secondary line under the Software row, or null. */
export function updateDetail(status: UpdateStatus, t: Translate): string | null {
  if (status.state === 'error' && status.error) return status.error;
  if (status.state === 'available') return t('update.changes', { count: String(status.behind ?? 0) });
  const last = status.last_result;
  if (!last) return null;
  if (last.ok && last.installed) return t('update.lastInstalled', { version: last.installed });
  if (!last.ok) return t('update.lastFailed');
  return null;
}

const STEP_KEYS: Record<string, MessageKey> = {
  'Stopping the radar': 'update.step.stopping',
  'Checking the download': 'update.step.checking',
  'Downloading the update': 'update.step.downloading',
  'Installing Python packages': 'update.step.python',
  'Installing interface packages': 'update.step.uiPackages',
  'Building the interface': 'update.step.building',
  'Checking the new version': 'update.step.verifying',
  'Rolling back': 'update.step.rollingBack',
};

/** Server step names are English; translate the known ones. */
export function updateStepLabel(step: string | null | undefined, t: Translate): string | undefined {
  if (!step) return undefined;
  const key = STEP_KEYS[step];
  return key ? t(key) : step;
}
