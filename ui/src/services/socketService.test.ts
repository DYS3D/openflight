import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fake = vi.hoisted(() => {
  const handlers = new Map<string, (...args: unknown[]) => void>();
  const managerHandlers = new Map<string, (...args: unknown[]) => void>();
  const socket = {
    on: (event: string, handler: (...args: unknown[]) => void) => {
      handlers.set(event, handler);
      return socket;
    },
    emit: vi.fn(() => socket),
    close: () => {},
    io: {
      on: (event: string, handler: (...args: unknown[]) => void) => {
        managerHandlers.set(event, handler);
      },
    },
  };
  return { handlers, managerHandlers, socket };
});

vi.mock('socket.io-client', () => ({ io: () => fake.socket }));

const { socketService } = await import('./socketService');

describe('socketService', () => {
  const emit = fake.socket.emit;

  beforeEach(() => {
    fake.handlers.clear();
    fake.managerHandlers.clear();
    emit.mockClear();
    socketService.connect();
  });

  afterEach(() => {
    socketService.disconnect();
  });

  it('sends the requested debug state explicitly instead of a bare toggle', () => {
    socketService.setDebugEnabled(true);
    socketService.setDebugEnabled(false);

    expect(emit).toHaveBeenCalledWith('toggle_debug', { enabled: true });
    expect(emit).toHaveBeenCalledWith('toggle_debug', { enabled: false });
    expect(emit).not.toHaveBeenCalledWith('toggle_debug');
  });
});
