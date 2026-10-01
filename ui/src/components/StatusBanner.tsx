import { useShallow } from 'zustand/react/shallow';
import { useBannerStore, type SimNotice } from '../stores/useBannerStore';
import { useI18n, type MessageKey } from '../i18n/useI18n';
import { radarNoiseDelta } from '../utils/radarHealth';
import './StatusBanner.css';

function noticeText(notice: SimNotice, t: (key: MessageKey, vars?: Record<string, string | number>) => string) {
  if (notice.kind === 'simSendFailed') {
    return t('banner.simSendFailed', { target: notice.target, reason: notice.reason });
  }
  if (notice.kind === 'deleteShotFailed') {
    return t('banner.deleteShotFailed', { reason: notice.reason });
  }
  return t('banner.simShotDropped', { reason: notice.reason });
}

export function StatusBanner() {
  const { t } = useI18n();
  const { notice, reconnectAttempt, levelWarning, radarHealth, dismissNotice } = useBannerStore(
    useShallow((state) => ({
      notice: state.notice,
      reconnectAttempt: state.reconnectAttempt,
      levelWarning: state.levelWarning,
      radarHealth: state.radarHealth,
      dismissNotice: state.dismissNotice,
    }))
  );
  const interference = radarHealth?.interference ? radarHealth : null;

  if (!notice && reconnectAttempt === null && !levelWarning && !interference) return null;

  return (
    <div className="status-banners">
      {reconnectAttempt !== null ? (
        <div className="status-banner status-banner--reconnecting" role="status">
          {t('banner.reconnecting', { n: reconnectAttempt })}
        </div>
      ) : null}
      {levelWarning ? (
        <div className="status-banner status-banner--level" role="status">
          {t('banner.notLevel', {
            pitch: levelWarning.pitch_deg.toFixed(1),
            roll: levelWarning.roll_deg.toFixed(1),
          })}
        </div>
      ) : null}
      {interference ? (
        <div className="status-banner status-banner--interference" role="status">
          {t('banner.radarInterference', { db: radarNoiseDelta(interference).toFixed(1) })}
        </div>
      ) : null}
      {notice ? (
        <div key={notice.id} className="status-banner status-banner--notice" role="alert" onClick={dismissNotice}>
          <span className="status-banner__text">{noticeText(notice, t)}</span>
          <button
            type="button"
            className="status-banner__dismiss"
            onClick={(event) => {
              event.stopPropagation();
              dismissNotice();
            }}
            aria-label={t('banner.dismissNotice')}
          >
            {t('live.dismiss')}
          </button>
        </div>
      ) : null}
    </div>
  );
}
