import { useLayoutEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from '../../i18n/useI18n';
import type { RadarLinkState } from '../../types/shot';
import type { RadarHealth } from '../../types/socket';
import { formatNoiseDelta } from '../../utils/radarHealth';

interface StatusMenuProps {
  connected: boolean;
  radarConnected: boolean;
  /** OPS243 link state; `reconnecting` while `--radar-auto-reconnect` re-detects it. */
  radarState?: RadarLinkState;
  /** IWR6843 link state, or null/undefined when the angle radar is not enabled. */
  iwr6843State?: RadarLinkState | null;
  /** Latest `radar_health`; the noise row is hidden until one arrives. */
  radarHealth?: RadarHealth | null;
  onClose: () => void;
}

function OverlayOnApp({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<Element | null>(null);

  useLayoutEffect(() => {
    // After commit, `.panel-app` is in the document. Looking it up here (not
    // during render) is how the status dim shares the footer menu's scrim.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- portal host exists only after layout
    setHost(document.querySelector('.panel-app'));
  }, []);

  if (!host) {
    return children;
  }
  return createPortal(children, host);
}

/**
 * Compact system readout anchored under the panel header LED + title.
 * Portaled onto `.panel-app` so the dim uses the same `.panel-scrim` as the
 * footer menu (absolute inset covering the whole kiosk, not just the header).
 */
export function StatusMenu({
  connected,
  radarConnected,
  radarState,
  iwr6843State,
  radarHealth,
  onClose,
}: StatusMenuProps) {
  const { t } = useI18n();
  const linkValue = (ok: boolean) => (ok ? t('header.connected') : t('header.disconnected'));
  const radarValue = (state: RadarLinkState) =>
    state === 'reconnecting' ? t('header.reconnecting') : linkValue(state === 'connected');
  const opsState: RadarLinkState = radarState ?? (radarConnected ? 'connected' : 'disconnected');

  return (
    <OverlayOnApp>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('header.closeStatus')} />
      <div className="panel-header__status-menu" role="dialog" aria-label={t('header.statusMenu')}>
        <div className="panel-header__status-row">
          <span className="panel-header__status-label">{t('header.server')}</span>
          <span className="panel-header__status-value">{linkValue(connected)}</span>
        </div>
        <div className="panel-header__status-row">
          <span className="panel-header__status-label">{t('header.radar')}</span>
          <span className="panel-header__status-value" data-state={opsState}>
            {radarValue(opsState)}
          </span>
        </div>
        {iwr6843State ? (
          <div className="panel-header__status-row">
            <span className="panel-header__status-label">{t('header.angleRadar')}</span>
            <span className="panel-header__status-value" data-state={iwr6843State}>
              {radarValue(iwr6843State)}
            </span>
          </div>
        ) : null}
        {radarHealth ? (
          <div className="panel-header__status-row">
            <span className="panel-header__status-label">{t('header.radarNoise')}</span>
            <span
              className="panel-header__status-value"
              data-state={radarHealth.interference ? 'interference' : 'quiet'}
            >
              {radarHealth.interference ? `${t('header.interference')} · ` : ''}
              {t('header.radarNoiseValue', {
                noise: radarHealth.noise_floor_db.toFixed(1),
                delta: formatNoiseDelta(radarHealth),
              })}
            </span>
          </div>
        ) : null}
      </div>
    </OverlayOnApp>
  );
}
