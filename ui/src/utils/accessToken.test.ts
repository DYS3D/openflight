import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ACCESS_TOKEN_HEADER,
  accessHeaders,
  getAccessToken,
  removeTokenFromAddressBar,
  resolveAccessToken,
} from './accessToken';

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    values,
  };
}

describe('access token', () => {
  it('reads the token from the URL and remembers it', () => {
    const storage = memoryStorage();
    expect(resolveAccessToken('?token=abc123', storage)).toBe('abc123');
    expect(resolveAccessToken('', storage)).toBe('abc123');
  });

  it('prefers a new URL token over the remembered one', () => {
    const storage = memoryStorage({ 'openflight.accessToken': 'old' });
    expect(resolveAccessToken('?token=new', storage)).toBe('new');
  });

  it('returns null when there is no token anywhere', () => {
    expect(resolveAccessToken('', memoryStorage())).toBeNull();
    expect(resolveAccessToken('?token=', null)).toBeNull();
  });

  it('survives storage that throws', () => {
    const broken = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
    };
    expect(resolveAccessToken('?token=abc', broken)).toBe('abc');
    expect(resolveAccessToken('', broken)).toBeNull();
  });

  it('builds the request header only when a token exists', () => {
    expect(accessHeaders('abc')).toEqual({ [ACCESS_TOKEN_HEADER]: 'abc' });
    expect(accessHeaders(null)).toEqual({});
  });

  describe('address bar', () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    function stubWindow(href: string, storage: ReturnType<typeof memoryStorage> | null) {
      const history = { state: { from: 'test' }, replaceState: vi.fn() };
      vi.stubGlobal('window', {
        location: new URL(href),
        history,
        get localStorage() {
          if (!storage) throw new Error('blocked');
          return storage;
        },
      });
      return history;
    }

    it('strips only the token and keeps the rest of the URL', () => {
      const history = { state: null, replaceState: vi.fn() };
      removeTokenFromAddressBar('http://pi.local:8080/app?token=abc&mode=range#shots', history);
      expect(history.replaceState).toHaveBeenCalledWith(null, '', '/app?mode=range#shots');
    });

    it('leaves a URL without a token alone', () => {
      const history = { state: null, replaceState: vi.fn() };
      removeTokenFromAddressBar('http://pi.local:8080/?mode=range', history);
      expect(history.replaceState).not.toHaveBeenCalled();
    });

    it('removes the token from the address bar once it is remembered', () => {
      const storage = memoryStorage();
      const history = stubWindow('http://pi.local:8080/?token=abc#live', storage);
      expect(getAccessToken()).toBe('abc');
      expect(storage.values.get('openflight.accessToken')).toBe('abc');
      expect(history.replaceState).toHaveBeenCalledWith({ from: 'test' }, '', '/#live');
    });

    it('keeps the token in the URL when storage is unavailable', () => {
      const history = stubWindow('http://pi.local:8080/?token=abc', null);
      expect(getAccessToken()).toBe('abc');
      expect(history.replaceState).not.toHaveBeenCalled();
    });
  });
});
