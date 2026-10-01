import { useEffect, useState } from 'react';
import { useI18n, type MessageKey } from '../../i18n/useI18n';
import {
  DISPLAY_PREFERENCE_KEYS,
  type DisplayPreferenceKey,
  type DisplayPreferences,
} from '../../stores/useDisplayPreferencesStore';
import { installedVoiceCount } from '../../utils/voiceCallout';

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

function useInstalledVoiceCount(): number {
  const [count, setCount] = useState(installedVoiceCount);

  useEffect(() => {
    const synth = typeof window === 'undefined' ? undefined : window.speechSynthesis;
    if (!synth) {
      return;
    }
    const update = () => setCount(installedVoiceCount());
    update();
    synth.addEventListener('voiceschanged', update);
    return () => synth.removeEventListener('voiceschanged', update);
  }, []);

  return count;
}

export function DisplayPreferencesSection({ preferences, onChange }: DisplayPreferencesSectionProps) {
  const { t } = useI18n();
  const voiceCount = useInstalledVoiceCount();

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
      {preferences.voiceCallout && voiceCount === 0 && (
        <span className="menu-sheet__note" role="status">
          {t('menu.voiceCalloutNoVoices')}
        </span>
      )}
    </section>
  );
}
