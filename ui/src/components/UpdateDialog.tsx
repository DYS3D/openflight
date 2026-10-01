import { useRef } from 'react';
import { useDragScroll } from '../hooks/useDragScroll';
import { ProgressIndicator } from './ProgressIndicator';
import { useI18n } from '../i18n/useI18n';
import type { UpdateStatus } from '../types/socket';
import { updateLabel, updateStepLabel } from '../utils/updateStatus';

/** Commit subjects listed in the confirmation; the rest are counted. */
const MAX_LISTED_CHANGES = 5;

interface UpdateDialogProps {
  status: UpdateStatus;
  /** The server's refusal of the last request (e.g. a shot is being processed). */
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * Confirm, follow and report a software update (`--update-check`).
 *
 * Driven entirely by the server's `update_status`: the dialog shows progress
 * while the server installs, then the restart notice. It has no close button
 * while the server is busy because the radar is already stopped.
 */
export function UpdateDialog({ status, error, onConfirm, onClose }: UpdateDialogProps) {
  const { t } = useI18n();
  const changesRef = useRef<HTMLUListElement>(null);
  const changesDragScroll = useDragScroll(changesRef);

  if (status.state === 'updating') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('update.pendingAria')}
        >
          <ProgressIndicator
            variant="dialog"
            title={t('update.pendingTitle')}
            detail={updateStepLabel(status.step, t)}
          />
        </div>
      </div>
    );
  }

  if (status.state === 'restarting') {
    const manual = status.restart === 'manual';
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('update.pendingAria')}
        >
          <ProgressIndicator
            variant="dialog"
            title={t('update.restartingTitle')}
            detail={manual ? t('update.restartManual') : t('update.restartingDetail')}
          />
        </div>
      </div>
    );
  }

  if (status.state === 'failed') {
    return (
      <div className="shutdown-overlay">
        <div className="shutdown-dialog" role="alertdialog" aria-modal="true" aria-labelledby="update-dialog-title">
          <p id="update-dialog-title">{t('update.failed')}</p>
          {status.error ? <span className="shutdown-dialog__error">{status.error}</span> : null}
          <span className="update-dialog__detail">
            {status.rolled_back ? t('update.failedRolledBack') : t('update.failedIncomplete')}
          </span>
          <div className="shutdown-dialog__buttons">
            <button className="shutdown-dialog__cancel" onClick={onClose} autoFocus>
              {t('update.close')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (status.state !== 'available' || !status.can_apply) {
    return (
      <div className="shutdown-overlay">
        <div className="shutdown-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title">
          <p id="update-dialog-title">
            {status.state === 'up_to_date' ? t('update.upToDateDetail') : updateLabel(status, t)}
          </p>
          {status.state === 'error' && status.error ? (
            <span className="shutdown-dialog__error">{status.error}</span>
          ) : null}
          <div className="shutdown-dialog__buttons">
            <button className="shutdown-dialog__cancel" onClick={onClose} autoFocus>
              {t('update.close')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const commits = status.commits ?? [];
  const behind = status.behind ?? commits.length;
  return (
    <div className="shutdown-overlay">
      <div
        className="shutdown-dialog update-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-dialog-title"
      >
        <p id="update-dialog-title">{t('update.confirmTitle')}</p>
        <span className="update-dialog__versions">
          {status.current} → {status.latest} · {t('update.changes', { count: String(behind) })}
        </span>
        {commits.length > 0 ? (
          <ul ref={changesRef} className="update-dialog__changes" {...changesDragScroll}>
            {commits.slice(0, MAX_LISTED_CHANGES).map((commit) => (
              <li key={commit.sha}>{commit.subject}</li>
            ))}
            {behind > MAX_LISTED_CHANGES ? <li>…</li> : null}
          </ul>
        ) : null}
        <span className="update-dialog__detail">{t('update.confirmDetail')}</span>
        {error ? <span className="shutdown-dialog__error">{error}</span> : null}
        <div className="shutdown-dialog__buttons">
          <button className="update-dialog__confirm" onClick={onConfirm} autoFocus>
            {t('update.install')}
          </button>
          <button className="shutdown-dialog__cancel" onClick={onClose}>
            {t('update.notNow')}
          </button>
        </div>
      </div>
    </div>
  );
}
