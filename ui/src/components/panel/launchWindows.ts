/**
 * Rough launch and spin windows per club for the Copper Live tiles. A guide,
 * not a fitting: the driver uses the common amateur 10–15° / 2,000–2,800 rpm
 * window; every other club centres on published tour averages with ±2.5° of
 * launch and ±15% of spin.
 */
export type RangeStatus = 'low' | 'in' | 'high';

interface Window {
  min: number;
  max: number;
}

interface ClubWindows {
  launch: Window;
  spin: Window;
}

const LAUNCH_TOLERANCE_DEG = 2.5;
const SPIN_TOLERANCE = 0.15;

// [launch °, spin rpm] centre points.
const CENTRES: Record<string, readonly [number, number]> = {
  '3-wood': [9.2, 3655],
  '5-wood': [9.4, 4350],
  '7-wood': [11, 4800],
  '9-wood': [12, 5200],
  '3-hybrid': [10.2, 4437],
  '5-hybrid': [11, 4800],
  '7-hybrid': [13, 5500],
  '9-hybrid': [15, 6200],
  '2-iron': [9.5, 4200],
  '3-iron': [10.4, 4630],
  '4-iron': [11, 4836],
  '5-iron': [12.1, 5361],
  '6-iron': [14.1, 6231],
  '7-iron': [16.3, 7097],
  '8-iron': [18.1, 7998],
  '9-iron': [20.4, 8647],
  pw: [24.2, 9304],
  gw: [26, 9800],
  sw: [29, 10000],
  lw: [32, 10200],
};

export function clubWindows(clubId: string): ClubWindows | null {
  if (clubId === 'driver') {
    return { launch: { min: 10, max: 15 }, spin: { min: 2000, max: 2800 } };
  }
  const centre = CENTRES[clubId];
  if (!centre) return null;
  const [launch, spin] = centre;
  return {
    launch: { min: launch - LAUNCH_TOLERANCE_DEG, max: launch + LAUNCH_TOLERANCE_DEG },
    spin: { min: Math.round(spin * (1 - SPIN_TOLERANCE)), max: Math.round(spin * (1 + SPIN_TOLERANCE)) },
  };
}

function classify(value: number, window: Window): RangeStatus {
  if (value < window.min) return 'low';
  if (value > window.max) return 'high';
  return 'in';
}

/** Where a shot's launch or spin sits against the club's window; null when unknown. */
export function rangeStatus(metricId: string, value: number | null, clubId: string): RangeStatus | null {
  if (value === null) return null;
  const windows = clubWindows(clubId);
  if (!windows) return null;
  if (metricId === 'launch_v') return classify(value, windows.launch);
  if (metricId === 'spin') return classify(value, windows.spin);
  return null;
}
