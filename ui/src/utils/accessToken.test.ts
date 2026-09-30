import { describe, expect, it } from 'vitest';
import { ACCESS_TOKEN_HEADER, accessHeaders, resolveAccessToken } from './accessToken';

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
});
