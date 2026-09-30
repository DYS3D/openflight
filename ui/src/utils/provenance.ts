import type { Shot } from '../types/shot';

function isModelledAngle(source: string | null | undefined, fallbackEstimated: boolean): boolean {
  if (source === 'estimated' || source === 'mock') return true;
  if (source) return false;
  return fallbackEstimated;
}

// Per-axis sources are authoritative; angle_source only covers older payloads.
export function isVerticalLaunchEstimated(shot: Shot): boolean {
  return (
    shot.launch_angle_vertical !== null &&
    isModelledAngle(shot.launch_angle_vertical_source, shot.angle_source === 'estimated')
  );
}

export function isHorizontalLaunchEstimated(shot: Shot): boolean {
  return (
    shot.launch_angle_horizontal !== null &&
    isModelledAngle(shot.launch_angle_horizontal_source, shot.angle_source === 'estimated')
  );
}

export function isSpinEstimated(shot: Shot): boolean {
  return shot.spin_rpm !== null && shot.spin_source === 'calculated';
}
