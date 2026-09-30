// Devices other than the kiosk are read-only unless they carry the device
// token. Opening the UI once with ?token=... remembers it on that device.
const STORAGE_KEY = 'openflight.accessToken';
export const ACCESS_TOKEN_HEADER = 'X-OpenFlight-Token';

type TokenStorage = Pick<Storage, 'getItem' | 'setItem'>;

function browserStorage(): TokenStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function resolveAccessToken(search: string, storage: TokenStorage | null): string | null {
  const fromUrl = new URLSearchParams(search).get('token')?.trim();
  if (fromUrl) {
    try {
      storage?.setItem(STORAGE_KEY, fromUrl);
    } catch {
      // Storage can be unavailable (private mode); the URL token still works.
    }
    return fromUrl;
  }
  try {
    return storage?.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function getAccessToken(): string | null {
  const search = typeof window === 'undefined' ? '' : window.location.search;
  return resolveAccessToken(search, browserStorage());
}

export function accessHeaders(token: string | null = getAccessToken()): Record<string, string> {
  return token ? { [ACCESS_TOKEN_HEADER]: token } : {};
}
