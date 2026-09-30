import { afterEach, describe, expect, it, vi } from 'vitest';
import { getServerOrigin, getServerPort } from './serverOrigin';

function stubLocation(url: string) {
  const { protocol, hostname, port, origin } = new URL(url);
  vi.stubGlobal('window', { location: { protocol, hostname, port, origin } });
}

describe('serverOrigin', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('defaults the server port to 8080', () => {
    vi.stubEnv('VITE_SERVER_PORT', '');
    expect(getServerPort()).toBe('8080');
  });

  it('reads the server port from VITE_SERVER_PORT', () => {
    vi.stubEnv('VITE_SERVER_PORT', '9090');
    expect(getServerPort()).toBe('9090');
  });

  it('points the Vite dev origin at the configured server port', () => {
    vi.stubEnv('VITE_SOCKET_URL', '');
    vi.stubEnv('VITE_SERVER_PORT', '9090');
    stubLocation('http://192.168.1.20:5173/');

    expect(getServerOrigin()).toBe('http://192.168.1.20:9090');
  });

  it('uses port 8080 on the Vite dev origin when no port is configured', () => {
    vi.stubEnv('VITE_SOCKET_URL', '');
    vi.stubEnv('VITE_SERVER_PORT', '');
    stubLocation('http://localhost:5173/');

    expect(getServerOrigin()).toBe('http://localhost:8080');
  });

  it('uses the page origin when the backend serves the built UI', () => {
    vi.stubEnv('VITE_SOCKET_URL', '');
    vi.stubEnv('VITE_SERVER_PORT', '9090');
    stubLocation('http://127.0.0.1:8080/');

    expect(getServerOrigin()).toBe('http://127.0.0.1:8080');
  });

  it('prefers an explicit VITE_SOCKET_URL', () => {
    vi.stubEnv('VITE_SOCKET_URL', 'http://example.test:7000');
    stubLocation('http://localhost:5173/');

    expect(getServerOrigin()).toBe('http://example.test:7000');
  });

  it('uses the configured port without a browser window', () => {
    vi.stubEnv('VITE_SOCKET_URL', '');
    vi.stubEnv('VITE_SERVER_PORT', '9090');
    vi.stubGlobal('window', undefined);

    expect(getServerOrigin()).toBe('http://localhost:9090');
  });
});
