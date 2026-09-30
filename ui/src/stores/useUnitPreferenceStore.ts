import { create } from 'zustand';
import type { UnitSystem } from '../utils/units';

const STORAGE_KEY = 'openflight.unit-system';

function readStoredUnitSystem(): UnitSystem {
  if (typeof window === 'undefined') {
    return 'imperial';
  }

  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'metric' ? 'metric' : 'imperial';
  } catch {
    return 'imperial';
  }
}

interface UnitPreferenceState {
  unitSystem: UnitSystem;
  setUnitSystem: (unitSystem: UnitSystem) => void;
}

export const useUnitPreferenceStore = create<UnitPreferenceState>((set) => ({
  unitSystem: readStoredUnitSystem(),
  setUnitSystem: (unitSystem) => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(STORAGE_KEY, unitSystem);
      } catch {
        // Storage can be unavailable (private mode, quota); the choice still
        // applies for this session.
      }
    }

    set({ unitSystem });
  },
}));
