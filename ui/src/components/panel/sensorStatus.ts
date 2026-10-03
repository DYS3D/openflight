import type { RadarLinkState } from '../../types/shot';
import type { SimStatus } from '../../types/socket';
import type { CameraCaptureSettings } from '../../stores/useCameraStore';

export type SensorLevel = 'ok' | 'warn' | 'off';

export interface SensorDot {
  id: string;
  label: string;
  level: SensorLevel;
}

const SIM_NAMES: Record<string, string> = {
  gspro: 'GSPro',
  opengolfsim: 'OpenGolfSim',
  partee: 'PAR-TEE',
};

function linkLevel(state: RadarLinkState): SensorLevel {
  if (state === 'connected') return 'ok';
  if (state === 'reconnecting') return 'warn';
  return 'off';
}

function simLevel(state: SimStatus['state']): SensorLevel {
  if (state === 'connected') return 'ok';
  if (state === 'connecting' || state === 'reconnecting') return 'warn';
  return 'off';
}

interface SensorInputs {
  opsState: RadarLinkState;
  iwr6843State?: RadarLinkState | null;
  camera: CameraCaptureSettings;
  simStatuses: Record<string, SimStatus>;
  labels: { ops: string; angle: string; camera: string };
}

/** One dot per enabled sensor or simulator; disabled ones are left out. */
export function buildSensorDots({ opsState, iwr6843State, camera, simStatuses, labels }: SensorInputs): SensorDot[] {
  const dots: SensorDot[] = [{ id: 'ops', label: labels.ops, level: linkLevel(opsState) }];
  if (iwr6843State) dots.push({ id: 'iwr6843', label: labels.angle, level: linkLevel(iwr6843State) });
  if (camera.available)
    dots.push({ id: 'camera', label: labels.camera, level: camera.running === false ? 'warn' : 'ok' });
  for (const sim of Object.values(simStatuses)) {
    dots.push({ id: `sim-${sim.target}`, label: SIM_NAMES[sim.target] ?? sim.target, level: simLevel(sim.state) });
  }
  return dots;
}
