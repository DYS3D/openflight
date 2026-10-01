import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { TriggerStatus } from '../types/shot';
import { latencyRows } from '../utils/shotLatency';
import { DebugPanel } from './DebugPanel';

const triggerStatus: TriggerStatus = {
  mode: 'rolling-buffer',
  trigger_type: 'magnitude',
  radar_connected: true,
  radar_state: 'connected',
  radar_port: '/dev/ttyACM0',
  iwr6843_state: null,
  triggers_total: 0,
  triggers_accepted: 0,
  triggers_rejected: 0,
};

function render(shotLatency?: Parameters<typeof latencyRows>[0]) {
  return renderToString(
    <DebugPanel
      enabled={false}
      readings={[]}
      shotLogs={[]}
      radarConfig={{ min_speed: 30, max_speed: 250, min_magnitude: 20, transmit_power: 7 }}
      mockMode={false}
      onToggle={() => {}}
      onUpdateConfig={() => {}}
      triggerDiagnostics={[]}
      triggerStatus={triggerStatus}
      shotLatency={shotLatency}
    />
  );
}

describe('latencyRows', () => {
  it('returns nothing without latency data', () => {
    expect(latencyRows(undefined)).toEqual([]);
    expect(latencyRows(null)).toEqual([]);
    expect(latencyRows({})).toEqual([]);
  });

  it('labels the known stages and passes extra server stages through', () => {
    expect(latencyRows({ trigger_to_ui: 182.4, trigger_to_final: 1210, camera_to_ui: 40, broken: undefined })).toEqual([
      { key: 'trigger_to_ui', label: 'Trigger → UI', ms: 182.4 },
      { key: 'trigger_to_final', label: 'Trigger → final', ms: 1210 },
      { key: 'camera_to_ui', label: 'camera to ui', ms: 40 },
    ]);
  });
});

describe('DebugPanel shot latency', () => {
  it('shows no latency table without a latency payload', () => {
    expect(render()).not.toContain('shot-latency');
    expect(render(null)).not.toContain('Shot latency');
  });

  it('lists the last shot latencies as a small table on the Status tab', () => {
    const html = render({ trigger_to_ui: 182.4, trigger_to_final: 1210.6 });

    expect(html).toContain('>Shot latency<');
    expect(html).toContain('class="shot-latency"');
    expect(html).toContain('>Trigger → UI</th><td>182</td>');
    expect(html).toContain('>Trigger → final</th><td>1211</td>');
  });
});
