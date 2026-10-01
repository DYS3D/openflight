import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { LevelStatus } from '../../types/socket';
import { LevelPanel } from './LevelPanel';

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

const level: LevelStatus = { pitch_deg: 0.2, roll_deg: -0.4, level: true, threshold_deg: 1 };
const tilted: LevelStatus = { pitch_deg: 2.6, roll_deg: -1.3, level: false, threshold_deg: 1 };

describe('LevelPanel', () => {
  it('explains the server flags when no level_status has arrived', () => {
    const html = text(renderToString(<LevelPanel status={null} />));

    expect(html).toContain('panel-header__title">Level<');
    expect(html).toContain('>No level data<');
    expect(html).toContain('--inclinometer --level-warning-deg');
    expect(html).not.toContain('level-vial');
  });

  it('draws a green bubble level with numeric readouts when level', () => {
    const html = text(renderToString(<LevelPanel status={level} />));

    expect(html).toContain('level-vial level-vial--level');
    expect(html).toContain('level-panel__body--level');
    expect(html).toContain('aria-label="Bubble level: pitch 0.2°, roll -0.4°"');
    expect(html).toContain('>Pitch<');
    expect(html).toContain('>0.2<span class="level-panel__readout-unit">°<');
    expect(html).toContain('>Roll<');
    expect(html).toContain('>-0.4<span class="level-panel__readout-unit">°<');
    expect(html).toContain('>Threshold ±1.0°<');
    expect(html).toContain('>Adjust the feet until both read 0.0°<');
    expect(html).toContain('level-panel__state">Level<');
  });

  it('turns amber and says not level once either axis exceeds the threshold', () => {
    const html = text(renderToString(<LevelPanel status={tilted} />));

    expect(html).toContain('level-vial level-vial--off');
    expect(html).toContain('level-panel__body--off');
    expect(html).toContain('level-panel__state">Not level<');
    expect(html).toContain('panel-header__subtitle">Not level<');
  });

  it('moves the bubble with the tilt and keeps it inside the vial', () => {
    const centred = renderToString(<LevelPanel status={{ ...level, pitch_deg: 0, roll_deg: 0 }} />);
    expect(centred).toContain('class="level-vial__bubble" cx="100" cy="100"');

    const bubble = renderToString(<LevelPanel status={tilted} />).match(
      /level-vial__bubble" cx="([\d.]+)" cy="([\d.]+)"/
    );
    expect(bubble).not.toBeNull();
    const cx = Number(bubble?.[1]);
    const cy = Number(bubble?.[2]);
    expect(cx).toBeLessThan(100);
    expect(cy).toBeLessThan(100);
    expect(Math.hypot(cx - 100, cy - 100)).toBeLessThanOrEqual(90 - 14 + 0.01);
  });
});
