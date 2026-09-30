import { useEffect, useCallback } from 'react';
import { socketService } from '../services/socketService';
import { accessHeaders } from '../utils/accessToken';

export function useSocket() {
  useEffect(() => {
    socketService.connect();
  }, []);

  const shutdown = useCallback(async () => {
    const response = await fetch('/api/shutdown', { method: 'POST', headers: accessHeaders() });
    if (!response.ok) {
      throw new Error(`Shutdown request failed (${response.status})`);
    }
  }, []);

  return { shutdown };
}
