import { useEffect } from 'react';
import { socketService } from '../services/socketService';
import { accessHeaders } from '../utils/accessToken';
import { getServerOrigin } from '../utils/serverOrigin';

export async function requestServerShutdown(): Promise<void> {
  const response = await fetch(`${getServerOrigin()}/api/shutdown`, { method: 'POST', headers: accessHeaders() });
  if (!response.ok) {
    throw new Error(`Shutdown request failed (${response.status})`);
  }
}

export function useSocket() {
  useEffect(() => {
    socketService.connect();
  }, []);

  return { shutdown: requestServerShutdown };
}
