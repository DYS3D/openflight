import { renderToString } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IWR6843Diagnostic } from '../types/shot';
import { IWR6843Alert } from './IWR6843Alert';

const debugState = vi.hoisted(() => ({
  iwr6843Alert: null as IWR6843Diagnostic | null,
  dismissIWR6843Alert: () => {},
}));

vi.mock('../stores/useDebugStore', () => ({
  useDebugStore: <T,>(selector: (state: typeof debugState) => T) => selector(debugState),
}));

describe('IWR6843Alert', () => {
  beforeEach(() => {
    debugState.iwr6843Alert = null;
  });

  it('renders nothing without a pending alert', () => {
    expect(renderToString(<IWR6843Alert />)).toBe('');
  });

  it('shows the failure reason with a dismiss button', () => {
    debugState.iwr6843Alert = { state: 'error', reason: 'serial timeout' };

    const html = renderToString(<IWR6843Alert />);

    expect(html).toContain('role="alert"');
    expect(html).toContain('serial timeout');
    expect(html).toContain('aria-label="Dismiss TI radar alert"');
  });
});
