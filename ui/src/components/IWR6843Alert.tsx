import { useShallow } from 'zustand/react/shallow';
import { useDebugStore } from '../stores/useDebugStore';
import { useI18n } from '../i18n/useI18n';

export function IWR6843Alert() {
  const { t } = useI18n();
  const { alert, dismiss } = useDebugStore(
    useShallow((state) => ({ alert: state.iwr6843Alert, dismiss: state.dismissIWR6843Alert }))
  );

  if (!alert) return null;

  return (
    <div className="iwr-alert" role="alert">
      <div>
        <strong>{t('live.tiRadarFailed')}</strong>
        <span>{t('live.tiRadarDetail', { reason: alert.reason })}</span>
      </div>
      <button type="button" onClick={dismiss} aria-label={t('live.dismissAlert')}>
        {t('live.dismiss')}
      </button>
    </div>
  );
}
