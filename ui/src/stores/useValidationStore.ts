import { create } from 'zustand';

export interface ValidationEntry {
  comparatorDevice: string;
  comparatorSpeed: string;
  notes: string;
}

interface ValidationState {
  entries: Record<string, ValidationEntry>;
  updateEntry: (timestamp: string, patch: Partial<ValidationEntry>) => void;
  removeEntry: (timestamp: string) => void;
  clearEntries: () => void;
}

const STORAGE_KEY = 'openflight-validation-entries';

const emptyEntry: ValidationEntry = {
  comparatorDevice: '',
  comparatorSpeed: '',
  notes: '',
};

export const MAX_VALIDATION_ENTRIES = 500;

function isValidationEntry(value: unknown): value is ValidationEntry {
  if (typeof value !== 'object' || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.comparatorDevice === 'string' &&
    typeof entry.comparatorSpeed === 'string' &&
    typeof entry.notes === 'string'
  );
}

function timestampOrder(timestamp: string): number {
  const time = Date.parse(timestamp);
  return Number.isNaN(time) ? Number.NEGATIVE_INFINITY : time;
}

function capEntries(entries: Record<string, ValidationEntry>): Record<string, ValidationEntry> {
  const timestamps = Object.keys(entries);
  if (timestamps.length <= MAX_VALIDATION_ENTRIES) return entries;

  const newest = timestamps.sort((a, b) => timestampOrder(b) - timestampOrder(a)).slice(0, MAX_VALIDATION_ENTRIES);
  return Object.fromEntries(newest.map((timestamp) => [timestamp, entries[timestamp]]));
}

function loadEntries(): Record<string, ValidationEntry> {
  if (typeof window === 'undefined') return {};

  let parsed: unknown;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    parsed = raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};

  const entries: Record<string, ValidationEntry> = {};
  for (const [timestamp, entry] of Object.entries(parsed)) {
    if (isValidationEntry(entry)) {
      entries[timestamp] = {
        comparatorDevice: entry.comparatorDevice,
        comparatorSpeed: entry.comparatorSpeed,
        notes: entry.notes,
      };
    }
  }
  return capEntries(entries);
}

function saveEntries(entries: Record<string, ValidationEntry>) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage can be unavailable (private mode, quota); entries still apply
    // for this session.
  }
}

export function getEmptyValidationEntry(): ValidationEntry {
  return { ...emptyEntry };
}

export const useValidationStore = create<ValidationState>((set) => ({
  entries: loadEntries(),
  updateEntry: (timestamp, patch) =>
    set((state) => {
      const updated = capEntries({
        ...state.entries,
        [timestamp]: {
          ...(state.entries[timestamp] ?? emptyEntry),
          ...patch,
        },
      });
      saveEntries(updated);
      return { entries: updated };
    }),
  removeEntry: (timestamp) =>
    set((state) => {
      const updated = { ...state.entries };
      delete updated[timestamp];
      saveEntries(updated);
      return { entries: updated };
    }),
  clearEntries: () => {
    saveEntries({});
    set({ entries: {} });
  },
}));
