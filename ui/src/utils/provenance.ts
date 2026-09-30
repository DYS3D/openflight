import type { Shot } from '../types/shot';

const MODELLED_SOURCES: ReadonlySet<string> = new Set(['estimated', 'mock']);

function isModelled(source: string | null | undefined): boolean {
  return source != null && MODELLED_SOURCES.has(source);
}

// Per-axis sources are authoritative; angle_source only covers older payloads.
function isAxisEstimated(axisSource: string | null | undefined, angleSource: string | null): boolean {
  return axisSource ? isModelled(axisSource) : isModelled(angleSource);
}

export function isVerticalLaunchEstimated(shot: Shot): boolean {
  return shot.launch_angle_vertical !== null && isAxisEstimated(shot.launch_angle_vertical_source, shot.angle_source);
}

export function isHorizontalLaunchEstimated(shot: Shot): boolean {
  return (
    shot.launch_angle_horizontal !== null && isAxisEstimated(shot.launch_angle_horizontal_source, shot.angle_source)
  );
}

export function isSpinEstimated(shot: Shot): boolean {
  return shot.spin_rpm !== null && (shot.spin_source === 'calculated' || shot.spin_source === 'mock');
}
