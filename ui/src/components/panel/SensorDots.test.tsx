import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SensorDots } from './SensorDots';
import { buildSensorDots } from './sensorStatus';

const labels = { ops: 'OPS', angle: 'TI', camera: 'Cam' };

describe('buildSensorDots', () => {
  it('lists only the OPS radar when nothing else is enabled', () => {
    expect(buildSensorDots({ opsState: 'connected', camera: { available: false }, simStatuses: {}, labels })).toEqual([
      { id: 'ops', label: 'OPS', level: 'ok' },
    ]);
  });

  it('adds the angle radar, camera, and each simulator with their levels', () => {
    const dots = buildSensorDots({
      opsState: 'reconnecting',
      iwr6843State: 'disconnected',
      camera: { available: true, running: true },
      simStatuses: { gspro: { target: 'gspro', state: 'connected' } },
      labels,
    });

    expect(dots.map((dot) => [dot.label, dot.level])).toEqual([
      ['OPS', 'warn'],
      ['TI', 'off'],
      ['Cam', 'ok'],
      ['GSPro', 'ok'],
    ]);
  });
});

describe('SensorDots', () => {
  it('labels each dot with its link state for screen readers', () => {
    const html = renderToString(<SensorDots dots={[{ id: 'ops', label: 'OPS', level: 'ok' }]} />);

    expect(html).toContain('aria-label="OPS: Connected"');
    expect(html).toContain('sensor-dots__item--ok');
  });
});
