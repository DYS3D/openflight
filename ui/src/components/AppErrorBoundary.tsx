import { Component, type ErrorInfo, type ReactNode } from 'react';
import { t } from '../i18n';
import { Button } from './ui/Button';
import './AppErrorBoundary.css';

export function ErrorFallback({ onReload }: { onReload: () => void }) {
  return (
    <div className="app-error" role="alert">
      <h1 className="app-error__title">{t('app.errorTitle')}</h1>
      <p className="app-error__detail">{t('app.errorDetail')}</p>
      <Button className="app-error__reload" onClick={onReload}>
        {t('app.reload')}
      </Button>
    </div>
  );
}

function reloadPage() {
  window.location.reload();
}

interface AppErrorBoundaryState {
  error: Error | null;
}

export class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled UI error', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return <ErrorFallback onReload={reloadPage} />;
    }
    return this.props.children;
  }
}
