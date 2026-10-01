import { afterEach, describe, expect, it, vi } from 'vitest';
import { requestServerShutdown } from './useSocket';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('requestServerShutdown', () => {
  it('posts to the server origin, not the page origin', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    await requestServerShutdown();

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8080/api/shutdown',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('reports a refused shutdown', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }));

    await expect(requestServerShutdown()).rejects.toThrow('Shutdown request failed (401)');
  });
});
