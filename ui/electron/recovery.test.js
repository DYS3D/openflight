import { describe, it, expect, vi } from 'vitest';
import { attachRecovery, MAX_RECOVERY_DELAY_MS, RECOVERY_DELAY_MS } from './recovery.js';

const TARGET = 'http://localhost:8080/';

function fakeWebContents() {
  const handlers = new Map();
  return {
    handlers,
    loadURL: vi.fn(),
    isDestroyed: () => false,
    on: (event, handler) => handlers.set(event, handler),
    emit: (event, ...args) => handlers.get(event)(...args),
  };
}

function setup() {
  const contents = fakeWebContents();
  const timers = [];
  const schedule = vi.fn((run, delay) => timers.push({ run, delay }));
  attachRecovery(contents, TARGET, schedule);
  return { contents, timers };
}

describe('attachRecovery', () => {
  it('reloads the target after the renderer process dies', () => {
    const { contents, timers } = setup();

    contents.emit('render-process-gone', {}, { reason: 'crashed' });
    expect(contents.loadURL).not.toHaveBeenCalled();
    expect(timers).toHaveLength(1);
    expect(timers[0].delay).toBe(RECOVERY_DELAY_MS);

    timers[0].run();
    expect(contents.loadURL).toHaveBeenCalledWith(TARGET);
  });

  it('reloads after a failed main-frame load but not for aborts or subframes', () => {
    const { contents, timers } = setup();

    contents.emit('did-fail-load', {}, -3, 'ERR_ABORTED', TARGET, true);
    contents.emit('did-fail-load', {}, -102, 'ERR_CONNECTION_REFUSED', TARGET, false);
    expect(timers).toHaveLength(0);

    contents.emit('did-fail-load', {}, -102, 'ERR_CONNECTION_REFUSED', TARGET, true);
    expect(timers).toHaveLength(1);
  });

  it('schedules one reload at a time and backs off while loads keep failing', () => {
    const { contents, timers } = setup();

    contents.emit('did-fail-load', {}, -102, 'ERR_CONNECTION_REFUSED', TARGET, true);
    contents.emit('render-process-gone', {}, { reason: 'crashed' });
    expect(timers).toHaveLength(1);

    for (let i = 0; i < 8; i += 1) {
      timers.at(-1).run();
      contents.emit('did-fail-load', {}, -102, 'ERR_CONNECTION_REFUSED', TARGET, true);
    }
    const delays = timers.map((timer) => timer.delay);
    expect(delays[1]).toBe(RECOVERY_DELAY_MS * 2);
    expect(Math.max(...delays)).toBe(MAX_RECOVERY_DELAY_MS);

    timers.at(-1).run();
    contents.emit('did-finish-load');
    contents.emit('render-process-gone', {}, { reason: 'crashed' });
    expect(timers.at(-1).delay).toBe(RECOVERY_DELAY_MS);
  });
});
