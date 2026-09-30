import { create } from 'zustand';

const STORAGE_KEY = 'openflight.display-preferences';

export interface DisplayPreferences {
  bigNumberAfterShot: boolean;
  consistencyColors: boolean;
  voiceCallout: boolean;
  showNormalizedCarry: boolean;
}

export type DisplayPreferenceKey = keyof DisplayPreferences;

export const DISPLAY_PREFERENCE_KEYS: readonly DisplayPreferenceKey[] = [
  'bigNumberAfterShot',
  'consistencyColors',
  'voiceCallout',
  'showNormalizedCarry',
];

export const DEFAULT_DISPLAY_PREFERENCES: DisplayPreferences = {
  bigNumberAfterShot: false,
  consistencyColors: false,
  voiceCallout: false,
  showNormalizedCarry: false,
};

function readStoredPreferences(): DisplayPreferences {
  if (typeof window === 'undefined') {
    return DEFAULT_DISPLAY_PREFERENCES;
  }

  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (!parsed || typeof parsed !== 'object') {
      return DEFAULT_DISPLAY_PREFERENCES;
    }
    const stored = parsed as Record<string, unknown>;
    const preferences = { ...DEFAULT_DISPLAY_PREFERENCES };
    for (const key of DISPLAY_PREFERENCE_KEYS) {
      preferences[key] = stored[key] === true;
    }
    return preferences;
  } catch {
    return DEFAULT_DISPLAY_PREFERENCES;
  }
}

interface DisplayPreferencesState {
  preferences: DisplayPreferences;
  setPreference: (key: DisplayPreferenceKey, value: boolean) => void;
}

export const useDisplayPreferencesStore = create<DisplayPreferencesState>((set, get) => ({
  preferences: readStoredPreferences(),
  setPreference: (key, value) => {
    const preferences = { ...get().preferences, [key]: value };
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
      } catch {
        // Storage can be unavailable (private mode, quota); keep it for this session.
      }
    }

    set({ preferences });
  },
}));
