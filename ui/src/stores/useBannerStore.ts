import { create } from 'zustand';
import type { LevelStatus } from '../types/socket';

export type SimNoticeInput =
  { kind: 'simSendFailed'; target: string; reason: string } | { kind: 'simShotDropped'; reason: string };

export type SimNotice = SimNoticeInput & { id: number };

export const NOTICE_TIMEOUT_MS = 6000;

interface BannerState {
  notice: SimNotice | null;
  reconnectAttempt: number | null;
  levelWarning: LevelStatus | null;
  showNotice: (notice: SimNoticeInput) => void;
  dismissNotice: () => void;
  setReconnectAttempt: (attempt: number) => void;
  clearReconnect: () => void;
  setLevelStatus: (status: LevelStatus) => void;
}

let nextNoticeId = 1;
let dismissTimer: ReturnType<typeof setTimeout> | null = null;

function cancelDismissTimer() {
  if (dismissTimer !== null) {
    clearTimeout(dismissTimer);
    dismissTimer = null;
  }
}

export const useBannerStore = create<BannerState>((set) => ({
  notice: null,
  reconnectAttempt: null,
  levelWarning: null,
  showNotice: (notice) => {
    cancelDismissTimer();
    set({ notice: { ...notice, id: nextNoticeId++ } });
    dismissTimer = setTimeout(() => {
      dismissTimer = null;
      set({ notice: null });
    }, NOTICE_TIMEOUT_MS);
  },
  dismissNotice: () => {
    cancelDismissTimer();
    set({ notice: null });
  },
  setReconnectAttempt: (attempt) => set({ reconnectAttempt: attempt }),
  clearReconnect: () => set({ reconnectAttempt: null }),
  setLevelStatus: (status) => set({ levelWarning: status.level ? null : status }),
}));
