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

type UrlHistory = Pick<History, 'replaceState' | 'state'>;

export function removeTokenFromAddressBar(href: string, history: UrlHistory): void {
  const url = new URL(href);
  if (!url.searchParams.has('token')) return;
  url.searchParams.delete('token');
  history.replaceState(history.state, '', `${url.pathname}${url.search}${url.hash}`);
}

function isRemembered(storage: TokenStorage | null, token: string): boolean {
  try {
    return storage?.getItem(STORAGE_KEY) === token;
  } catch {
    return false;
  }
}

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  const storage = browserStorage();
  const token = resolveAccessToken(window.location.search, storage);
  // Keep the token in the URL when storage is unavailable; it is the only copy.
  if (token && isRemembered(storage, token)) {
    removeTokenFromAddressBar(window.location.href, window.history);
  }
  return token;
}

export function accessHeaders(token: string | null = getAccessToken()): Record<string, string> {
  return token ? { [ACCESS_TOKEN_HEADER]: token } : {};
}

// <img> and <video> cannot send the header, so media URLs carry ?token= instead.
export function withAccessToken(url: string, token: string | null = getAccessToken()): string {
  if (!token) return url;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}
