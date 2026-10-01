import { create } from 'zustand';
import type { Shot } from '../types/shot';
import type { PracticeConfig } from '../utils/practice';

export interface PracticeSession {
  config: PracticeConfig;
  /** Shots already in the session when the round started; they are never scored. */
  baseline: ReadonlySet<string>;
}

export function startPracticeSession(config: PracticeConfig, shots: readonly Shot[]): PracticeSession {
  return { config, baseline: new Set(shots.map((shot) => shot.timestamp)) };
}

/**
 * Practice rounds keyed by golfer, kept outside the panel so switching tabs keeps the
 * round. `lastConfig` lets a golfer's first round reuse the game settings in use.
 */
interface PracticeState {
  sessions: Readonly<Record<string, PracticeSession>>;
  lastConfig: PracticeConfig | null;
  setSession: (profileId: string, session: PracticeSession) => void;
}

export const usePracticeStore = create<PracticeState>((set) => ({
  sessions: {},
  lastConfig: null,
  setSession: (profileId, session) =>
    set((state) => ({ sessions: { ...state.sessions, [profileId]: session }, lastConfig: session.config })),
}));
