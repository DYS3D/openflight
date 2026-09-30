export const DEFAULT_SERVER_PORT = '8080';

export function getServerPort(): string {
  return import.meta.env.VITE_SERVER_PORT || DEFAULT_SERVER_PORT;
}

export function getServerOrigin(): string {
  if (import.meta.env.VITE_SOCKET_URL) {
    return import.meta.env.VITE_SOCKET_URL;
  }

  if (typeof window === 'undefined') {
    return `http://localhost:${getServerPort()}`;
  }

  const { protocol, hostname, port, origin } = window.location;

  if (port === '5173') {
    return `${protocol}//${hostname}:${getServerPort()}`;
  }

  return origin;
}
