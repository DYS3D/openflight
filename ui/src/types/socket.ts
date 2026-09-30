export interface DebugReading {
  speed: number;
  direction: 'inbound' | 'outbound' | 'unknown';
  magnitude: number | null;
  timestamp: string;
}

export type SimState = 'connected' | 'connecting' | 'reconnecting' | 'disabled' | 'stopped' | 'error';

export interface SimStatus {
  target: string;
  state: SimState;
  host?: string;
  port?: number;
  message?: string;
  attempt?: number;
  next_retry_in_s?: number;
}

export interface SimShotInfo {
  target: string;
  shot_number: number;
  fields: string[];
  values: Record<string, number | null>;
  provenance: Record<string, 'measured' | 'estimated'>;
}

export interface SwingSpeedEvent {
  peak_speed_mph: number;
  timestamp: string;
  duration_ms: number;
  reading_count: number;
  trigger_speed_mph: number;
  peak_magnitude: number | null;
  profile_id?: string;
  profile_name?: string;
  unit: string;
  mode: 'swing-speed';
}

export interface RadarConfig {
  min_speed: number;
  max_speed: number;
  min_magnitude: number;
  transmit_power: number;
}

export interface DebugShotLog {
  type: 'shot';
  timestamp: string;
  radar: {
    ball_speed_mph: number;
    club_speed_mph: number | null;
    smash_factor: number | null;
    peak_magnitude: number;
  };
  camera: {
    launch_angle_vertical: number;
    launch_angle_horizontal: number;
    launch_angle_confidence: number;
    positions_tracked: number;
    launch_detected: boolean;
  } | null;
  club: string;
}

/** `update_status` from the server (`--update-check`). `enabled: false` hides all update UI. */
export type UpdateState =
  'disabled' | 'idle' | 'checking' | 'up_to_date' | 'available' | 'updating' | 'restarting' | 'failed' | 'error';

export interface UpdateCommit {
  sha: string;
  subject: string;
}

export interface UpdateResult {
  ok: boolean;
  previous: string | null;
  installed: string | null;
  error: string | null;
  rolled_back: boolean;
  finished_at: string;
  log_path: string | null;
}

export interface UpdateStatus {
  enabled: boolean;
  state: UpdateState;
  remote?: string;
  branch?: string;
  current?: string | null;
  current_date?: string | null;
  latest?: string | null;
  behind?: number;
  commits?: UpdateCommit[];
  checked_at?: string | null;
  error?: string | null;
  step?: string | null;
  rolled_back?: boolean;
  /** True only for the Pi's own touchscreen; phones see the status read-only. */
  can_apply?: boolean;
  /** `systemd` restarts the service by itself; `manual` needs a relaunch. */
  restart?: 'systemd' | 'manual';
  last_result?: UpdateResult | null;
}

/** `level_status` from the server (`--level-warning-deg`); never sent by default. */
export interface LevelStatus {
  pitch_deg: number;
  roll_deg: number;
  level: boolean;
  threshold_deg: number;
}
