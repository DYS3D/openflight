import { useRef } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { useSystemStore } from '../../stores/useSystemStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useDisplayPreferencesStore } from '../../stores/useDisplayPreferencesStore';
import { useDragScroll } from '../../hooks/useDragScroll';
import { DisplayPreferencesSection } from './DisplayPreferencesSection';
import { SegmentedControl } from '../ui/SegmentedControl';
import { SimStatus } from '../SimStatus';
import type { UpdateStatus } from '../../types/socket';
import { canCheckForUpdates, canInstallUpdate, updateDetail, updateLabel } from '../../utils/updateStatus';

interface MenuSheetProps {
  onClose: () => void;
  onShutdown: () => void;
  /** `update_status` from the server; the Software row is hidden unless `enabled`. */
  updateStatus?: UpdateStatus | null;
  onOpenUpdate?: () => void;
  onCheckUpdates?: () => void;
  onOpenPractice?: () => void;
  onOpenLevel?: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Units / Shut down. Profiles live on their own panel. The System
 * block is an addition: the mockup replaced the old top header, and simulator
 * state had nowhere else to go. Battery lives in the footer.
 * Socket connection lives on the panel header LED.
 */
export function MenuSheet({
  onClose,
  onShutdown,
  updateStatus = null,
  onOpenUpdate,
  onCheckUpdates,
  onOpenPractice,
  onOpenLevel,
}: MenuSheetProps) {
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { preferences, setPreference } = useDisplayPreferencesStore();
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(sheetRef);

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('menu.close')} />
      <div
        className="menu-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={t('menu.title')}
        ref={sheetRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.units')}</span>
          <SegmentedControl
            ariaLabel={t('menu.displayUnits')}
            value={unitSystem}
            options={[
              { id: 'imperial', label: 'MPH / YDS' },
              { id: 'metric', label: 'KMH / M' },
            ]}
            onChange={setUnitSystem}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.theme')}</span>
          <SegmentedControl
            ariaLabel={t('menu.theme')}
            value={theme}
            options={[
              { id: 'dark', label: t('menu.themeDark') },
              { id: 'light', label: t('menu.themeLight') },
              { id: 'copper', label: t('menu.themeCopper') },
            ]}
            onChange={setTheme}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.system')}</span>
          {onOpenPractice || onOpenLevel ? (
            <div className="menu-sheet__tools">
              {onOpenPractice ? (
                <button type="button" className="menu-sheet__practice" onClick={onOpenPractice}>
                  {t('practice.title')}
                </button>
              ) : null}
              {onOpenLevel ? (
                <button type="button" className="menu-sheet__practice" onClick={onOpenLevel}>
                  {t('level.title')}
                </button>
              ) : null}
            </div>
          ) : null}
          {Object.keys(simStatuses).length > 0 ? (
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">{t('menu.simulators')}</span>
              <SimStatus statuses={simStatuses} />
            </div>
          ) : null}
          {updateStatus?.enabled ? (
            <div className="menu-sheet__update">
              <div className="menu-sheet__status-row">
                <span className="menu-sheet__status-label">{t('menu.software')}</span>
                <span className="menu-sheet__status-value" data-state={updateStatus.state}>
                  {updateLabel(updateStatus, t)}
                </span>
              </div>
              {updateDetail(updateStatus, t) ? (
                <span className="menu-sheet__update-detail">{updateDetail(updateStatus, t)}</span>
              ) : null}
              {canInstallUpdate(updateStatus) ? (
                <button type="button" className="menu-sheet__update-button" onClick={onOpenUpdate}>
                  {t('update.open')}
                </button>
              ) : null}
              {canCheckForUpdates(updateStatus) ? (
                <button type="button" className="menu-sheet__update-button" onClick={onCheckUpdates}>
                  {t('update.checkNow')}
                </button>
              ) : null}
            </div>
          ) : null}
        </section>

        <DisplayPreferencesSection preferences={preferences} onChange={setPreference} />

        <button type="button" className="menu-sheet__shutdown" onClick={onShutdown}>
          {t('menu.shutdown')}
        </button>
      </div>
    </>
  );
}
