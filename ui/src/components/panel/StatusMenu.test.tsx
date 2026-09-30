import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { StatusMenu } from './StatusMenu';

describe('StatusMenu', () => {
  it('states server and radar connectivity in words', () => {
    const html = renderToString(<StatusMenu connected radarConnected={false} onClose={() => {}} />);

    expect(html).toContain('class="panel-scrim"');
    expect(html).toContain('aria-label="Close status"');
    expect(html).toContain('aria-label="System status"');
    expect(html).toContain('>Server<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Connected<');
    expect(html).toContain('>Disconnected<');
    expect(html).not.toContain('Angle radar');
  });

  it('shows the radar as reconnecting while auto-reconnect re-detects it', () => {
    const html = renderToString(
      <StatusMenu connected radarConnected={false} radarState="reconnecting" onClose={() => {}} />
    );

    expect(html).toContain('data-state="reconnecting"');
    expect(html).toContain('>Reconnecting…<');
    expect(html).not.toContain('>Disconnected<');
  });

  it('adds an angle radar row only when the IWR6843 reports a link state', () => {
    const html = renderToString(
      <StatusMenu connected radarConnected radarState="connected" iwr6843State="reconnecting" onClose={() => {}} />
    );

    expect(html).toContain('>Angle radar<');
    expect(html).toContain('>Reconnecting…<');
    expect(html).toContain('>Connected<');
  });
});
