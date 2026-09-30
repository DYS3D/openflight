import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CameraPanel } from './CameraPanel';

describe('CameraPanel', () => {
  it('renders the high-speed capture workspace', () => {
    const html = renderToString(
      <CameraPanel
        captureSettings={{ available: true, running: true, armed: true, width: 320, height: 200, fps: 600 }}
        captureSettingsError={null}
        onUpdateCaptureSettings={() => {}}
      />
    );

    expect(html).toContain('camera-panel--capture');
    expect(html).toContain('Camera setup');
    expect(html).toContain('rolling buffer');
  });

  it('offers a Show ball zone switch that starts off and draws nothing', () => {
    const html = renderToString(
      <CameraPanel
        captureSettings={{ available: true, alignment_x_pct: 48, alignment_y_pct: 55 }}
        captureSettingsError={null}
        onUpdateCaptureSettings={() => {}}
      />
    );

    expect(html).toMatch(/role="switch" aria-checked="false"[^>]*camera-panel__switch/);
    expect(html).toContain('>Show ball zone<');
    expect(html).not.toContain('camera-feed__ball-zone');
  });

  it('marks the switch on when the zone is shown', () => {
    const html = renderToString(
      <CameraPanel
        captureSettings={{ available: true }}
        captureSettingsError={null}
        onUpdateCaptureSettings={() => {}}
        showBallZone
      />
    );

    expect(html).toMatch(/role="switch" aria-checked="true"[^>]*camera-panel__switch/);
  });
});
