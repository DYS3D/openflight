import { renderToString } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const accessedDebugKeys = vi.hoisted(() => new Set<string>());

vi.mock('./stores/useDebugStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./stores/useDebugStore')>();
  const real = actual.useDebugStore;
  type DebugState = ReturnType<typeof real.getState>;
  const tracked = <T,>(selector: (state: DebugState) => T) =>
    real((state) =>
      selector(
        new Proxy(state, {
          get(target, key, receiver) {
            if (typeof key === 'string') accessedDebugKeys.add(key);
            return Reflect.get(target, key, receiver);
          },
        })
      )
    );
  return { ...actual, useDebugStore: Object.assign(tracked, real) };
});

const { default: App } = await import('./App');
const { DebugView } = await import('./components/DebugView');

const HIGH_FREQUENCY_DEBUG_KEYS = ['debugReadings', 'debugShotLogs', 'triggerDiagnostics'];

describe('debug store subscriptions', () => {
  beforeEach(() => {
    accessedDebugKeys.clear();
  });

  it('keeps the app shell off the high-frequency debug data', () => {
    renderToString(<App />);

    expect(accessedDebugKeys.has('triggerStatus')).toBe(true);
    for (const key of HIGH_FREQUENCY_DEBUG_KEYS) {
      expect(accessedDebugKeys.has(key), key).toBe(false);
    }
  });

  it('reads debug readings only inside the Debug view', () => {
    const html = renderToString(<DebugView />);

    expect(html).toContain('panel-app__debug');
    for (const key of HIGH_FREQUENCY_DEBUG_KEYS) {
      expect(accessedDebugKeys.has(key), key).toBe(true);
    }
  });
});
