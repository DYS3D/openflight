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
const { useBannerStore } = await import('../stores/useBannerStore');
const { useDebugStore } = await import('../stores/useDebugStore');
const { useSystemStore } = await import('../stores/useSystemStore');

function fire(handlers: Map<string, (...args: unknown[]) => void>, event: string, ...args: unknown[]) {
  const handler = handlers.get(event);
  if (!handler) throw new Error(`no handler registered for ${event}`);
  handler(...args);
}

describe('socketService', () => {
  const emit = fake.socket.emit;

  beforeEach(() => {
    fake.handlers.clear();
    fake.managerHandlers.clear();
    emit.mockClear();
    useBannerStore.getState().dismissNotice();
    useBannerStore.getState().clearReconnect();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'log').mockImplementation(() => {});
    socketService.connect();
  });

  afterEach(() => {
    socketService.disconnect();
    useBannerStore.getState().dismissNotice();
    vi.restoreAllMocks();
  });

  it('sends the requested debug state explicitly instead of a bare toggle', () => {
    socketService.setDebugEnabled(true);
    socketService.setDebugEnabled(false);

    expect(emit).toHaveBeenCalledWith('toggle_debug', { enabled: true });
    expect(emit).toHaveBeenCalledWith('toggle_debug', { enabled: false });
    expect(emit).not.toHaveBeenCalledWith('toggle_debug');
  });

  it('stores the radar link state carried by trigger_status', () => {
    const initial = useDebugStore.getState().triggerStatus;
    expect(initial.radar_state).toBe('disconnected');
    expect(initial.iwr6843_state).toBeNull();

    fire(fake.handlers, 'trigger_status', {
      ...initial,
      radar_connected: false,
      radar_state: 'reconnecting',
      radar_port: '/dev/openflight-ops243',
      iwr6843_state: 'connected',
    });

    expect(useDebugStore.getState().triggerStatus).toMatchObject({
      radar_connected: false,
      radar_state: 'reconnecting',
      iwr6843_state: 'connected',
    });

    useDebugStore.getState().setTriggerStatus(initial);
  });

  it('shows a level warning from level_status and clears it when level again', () => {
    fire(fake.handlers, 'level_status', { pitch_deg: 3.2, roll_deg: 0.4, level: false, threshold_deg: 2 });
    expect(useBannerStore.getState().levelWarning).toMatchObject({ pitch_deg: 3.2, roll_deg: 0.4 });

    fire(fake.handlers, 'level_status', { pitch_deg: 0.1, roll_deg: 0.4, level: true, threshold_deg: 2 });
    expect(useBannerStore.getState().levelWarning).toBeNull();
  });

  it('surfaces sim_send_failed as an on-screen notice', () => {
    fire(fake.handlers, 'sim_send_failed', { target: 'gspro', reason: 'connection refused' });

    expect(useBannerStore.getState().notice).toMatchObject({
      kind: 'simSendFailed',
      target: 'gspro',
      reason: 'connection refused',
    });
  });

  it('surfaces sim_shot_dropped as an on-screen notice', () => {
    fire(fake.handlers, 'sim_shot_dropped', { reason: 'simulator busy' });

    expect(useBannerStore.getState().notice).toMatchObject({ kind: 'simShotDropped', reason: 'simulator busy' });
  });

  it('shows the reconnect attempt count and clears it on reconnect', () => {
    fire(fake.managerHandlers, 'reconnect_attempt', 1);
    fire(fake.managerHandlers, 'reconnect_attempt', 2);
    expect(useBannerStore.getState().reconnectAttempt).toBe(2);

    fire(fake.managerHandlers, 'reconnect', 2);
    expect(useBannerStore.getState().reconnectAttempt).toBeNull();
  });

  it('clears the reconnect banner when the socket connects', () => {
    fire(fake.managerHandlers, 'reconnect_attempt', 4);
    fire(fake.handlers, 'connect');

    expect(useBannerStore.getState().reconnectAttempt).toBeNull();
  });

  it('clears the reconnect banner when the service disconnects', () => {
    fire(fake.managerHandlers, 'reconnect_attempt', 5);
    socketService.disconnect();

    expect(useBannerStore.getState().reconnectAttempt).toBeNull();
  });

  describe('software updates', () => {
    afterEach(() => {
      useSystemStore.setState({ updateStatus: null, updateError: null });
    });

    it('asks for the update status on connect', () => {
      fire(fake.handlers, 'connect');
      expect(emit).toHaveBeenCalledWith('get_update_status');
    });

    it('stores status and refusals', () => {
      fire(fake.handlers, 'update_error', { error: 'A shot is being processed' });
      expect(useSystemStore.getState().updateError).toBe('A shot is being processed');

      fire(fake.handlers, 'update_status', { enabled: true, state: 'available', can_apply: true });
      expect(useSystemStore.getState().updateStatus).toMatchObject({ state: 'available' });
      expect(useSystemStore.getState().updateError).toBeNull();
    });

    it('sends check and apply requests', () => {
      useSystemStore.getState().setUpdateError('old');
      socketService.checkForUpdates();
      socketService.applyUpdate();
      expect(emit).toHaveBeenCalledWith('check_for_updates');
      expect(emit).toHaveBeenCalledWith('apply_update');
      expect(useSystemStore.getState().updateError).toBeNull();
    });

    it('reloads when the restarted server first speaks, even before connect fires', () => {
      const reload = vi.spyOn(socketService, 'reloadPage').mockImplementation(() => {});
      fire(fake.handlers, 'update_status', { enabled: true, state: 'restarting' });
      fire(fake.handlers, 'disconnect');
      // socket.io flushes buffered events from the new server before 'connect'.
      fire(fake.handlers, 'update_status', { enabled: true, state: 'up_to_date' });
      expect(reload).toHaveBeenCalledTimes(1);
      expect(useSystemStore.getState().updateStatus?.state).toBe('restarting');
    });

    it('also reloads from the connect event when no status arrives first', () => {
      const reload = vi.spyOn(socketService, 'reloadPage').mockImplementation(() => {});
      fire(fake.handlers, 'update_status', { enabled: true, state: 'failed' });
      fire(fake.handlers, 'disconnect');
      fire(fake.handlers, 'connect');
      expect(reload).toHaveBeenCalledTimes(1);
      expect(emit).not.toHaveBeenCalledWith('get_session');
    });

    it('does not reload on an ordinary reconnect', () => {
      const reload = vi.spyOn(socketService, 'reloadPage').mockImplementation(() => {});
      fire(fake.handlers, 'update_status', { enabled: true, state: 'up_to_date' });
      fire(fake.handlers, 'disconnect');
      fire(fake.handlers, 'update_status', { enabled: true, state: 'up_to_date' });
      fire(fake.handlers, 'connect');
      expect(reload).not.toHaveBeenCalled();
    });
  });

  it('stores radar_health in the banner store', () => {
    const health = { interference: true, noise_floor_db: -58.5, baseline_db: -62, updated_at: '2026-09-30T10:00:00Z' };
    fire(fake.handlers, 'radar_health', health);

    expect(useBannerStore.getState().radarHealth).toEqual(health);
  });
});
