/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const serverPort = loadEnv(mode, process.cwd(), 'VITE_').VITE_SERVER_PORT || '8080';

  return {
    plugins: [react()],
    server: {
      // Relative fetch('/api/shutdown') from the UI hits Vite; proxy to the mock/backend.
      proxy: {
        '/api': {
          target: `http://localhost:${serverPort}`,
          changeOrigin: true,
        },
      },
    },
    test: {
      include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}', 'electron/**/*.test.js'],
      exclude: ['tests/e2e/**'],
    },
  };
});
