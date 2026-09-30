import { useI18n, type MessageKey } from '../../i18n/useI18n';
import {
  DISPLAY_PREFERENCE_KEYS,
  type DisplayPreferenceKey,
  type DisplayPreferences,
} from '../../stores/useDisplayPreferencesStore';

const LABELS: Record<DisplayPreferenceKey, MessageKey> = {
  bigNumberAfterShot: 'menu.bigNumberAfterShot',
  consistencyColors: 'menu.consistencyColors',
  voiceCallout: 'menu.voiceCallout',
  showNormalizedCarry: 'menu.showNormalizedCarry',
  moreMetrics: 'menu.moreMetrics',
};

interface DisplayPreferencesSectionProps {
  preferences: DisplayPreferences;
  onChange: (key: DisplayPreferenceKey, value: boolean) => void;
}

export function DisplayPreferencesSection({ preferences, onChange }: DisplayPreferencesSectionProps) {
  const { t } = useI18n();

  return (
    <section className="menu-sheet__section menu-sheet__section--display">
      <span className="menu-sheet__section-title">{t('menu.display')}</span>
      {DISPLAY_PREFERENCE_KEYS.map((key) => (
        <button
          key={key}
          type="button"
          role="switch"
          aria-checked={preferences[key]}
          className="menu-sheet__switch"
          onClick={() => onChange(key, !preferences[key])}
        >
          <span className="menu-sheet__switch-label">{t(LABELS[key])}</span>
          <span className="menu-sheet__switch-track" aria-hidden="true">
            <span className="menu-sheet__switch-thumb" />
          </span>
        </button>
      ))}
    </section>
  );
}
