import { isValidElement, type ReactElement } from 'react';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setActiveLocale } from '../i18n';
import { AppErrorBoundary, ErrorFallback } from './AppErrorBoundary';

function renderBoundary(error: Error | null) {
  const boundary = new AppErrorBoundary({ children: <p>app content</p> });
  boundary.state = { error };
  return boundary.render();
}

describe('AppErrorBoundary', () => {
  afterEach(() => {
    setActiveLocale('en');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders children while nothing has failed', () => {
    expect(renderToString(<>{renderBoundary(null)}</>)).toBe('<p>app content</p>');
  });

  it('captures a thrown render error into state', () => {
    const error = new Error('boom');
    expect(AppErrorBoundary.getDerivedStateFromError(error)).toEqual({ error });
  });

  it('shows a friendly message and a Reload button after a crash', () => {
    const html = renderToString(<>{renderBoundary(new Error('boom'))}</>);

    expect(html).toContain('role="alert"');
    expect(html).toContain('Something went wrong');
    expect(html).toContain('app-error__reload');
    expect(html).toContain('>Reload</button>');
    expect(html).not.toContain('app content');
    expect(html).not.toContain('boom');
  });

  it('reloads the page when Reload is pressed', () => {
    const reload = vi.fn();
    vi.stubGlobal('window', { location: { reload } });

    const fallback = renderBoundary(new Error('boom'));
    expect(isValidElement(fallback)).toBe(true);
    (fallback as ReactElement<{ onReload: () => void }>).props.onReload();

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('logs the caught error for diagnostics', () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const boundary = new AppErrorBoundary({ children: null });
    const error = new Error('boom');

    boundary.componentDidCatch(error, { componentStack: '\n    at Broken' });

    expect(log).toHaveBeenCalledWith('Unhandled UI error', error, '\n    at Broken');
  });

  it('uses the active locale', () => {
    setActiveLocale('es');
    const html = renderToString(<ErrorFallback onReload={() => {}} />);

    expect(html).toContain('Algo salió mal');
    expect(html).toContain('>Recargar</button>');
  });
});
