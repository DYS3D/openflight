import type { CameraCaptureSettings } from '../stores/useCameraStore';

/** Where the ball should sit when the capture settings carry no alignment. */
export const DEFAULT_BALL_ZONE_CENTER = { x: 50, y: 78 } as const;

/** Side of the zone as a percentage of the frame, on both axes. */
export const DEFAULT_BALL_ZONE_SIZE_PCT = 12;

/** Ball-zone rectangle in percent of the preview frame, kept inside the frame. */
export interface BallZoneRect {
  left: number;
  top: number;
  width: number;
  height: number;
  /** Crosshair centre; equals the alignment point unless the box had to slide inward. */
  centerX: number;
  centerY: number;
}

function clampCenter(center: number, half: number): number {
  if (Number.isNaN(center)) return 50;
  return Math.min(100 - half, Math.max(half, center));
}

/**
 * Translucent target box around the alignment point from the camera settings.
 * A point near the frame edge slides the box inward so it stays fully visible.
 */
export function ballZoneRect(
  settings: Pick<CameraCaptureSettings, 'alignment_x_pct' | 'alignment_y_pct'>,
  sizePct: number = DEFAULT_BALL_ZONE_SIZE_PCT
): BallZoneRect {
  const size = Math.min(100, Math.max(1, sizePct));
  const half = size / 2;
  const centerX = clampCenter(settings.alignment_x_pct ?? DEFAULT_BALL_ZONE_CENTER.x, half);
  const centerY = clampCenter(settings.alignment_y_pct ?? DEFAULT_BALL_ZONE_CENTER.y, half);
  return { left: centerX - half, top: centerY - half, width: size, height: size, centerX, centerY };
}
