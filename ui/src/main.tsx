import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { AppErrorBoundary } from './components/AppErrorBoundary';
import { applyTheme, readStoredTheme } from './theme/theme';
import { useLocaleStore } from './stores/useLocaleStore';
import { primeSpeechOnFirstGesture } from './utils/voiceCallout';

applyTheme(readStoredTheme());
useLocaleStore.getState();
primeSpeechOnFirstGesture();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>
);
