// Kiosk shell for the OpenFlight React UI. Loads whatever URL the launcher
// script gives it (the startup splash, then the app itself once it
// navigates there) in a chromeless, fullscreen window — this replaces
// scripts/start-kiosk.sh's old system-browser detection (chromium-browser /
// chromium / google-chrome / firefox) with one pinned Chromium version.

import { app, BrowserWindow, Menu } from 'electron';
import { resolveTargetUrl } from './resolveTargetUrl.js';
import { attachRecovery } from './recovery.js';

const targetUrl = resolveTargetUrl(process.env, process.argv);

Menu.setApplicationMenu(null);

// On a Raspberry Pi ANGLE tries desktop OpenGL first, fails ("Could not create
// a backing OpenGL context") and falls back to OpenGL ES. Ask for ES directly.
if (process.platform === 'linux' && process.arch === 'arm64') {
  app.commandLine.appendSwitch('use-angle', 'gles');
}

function createWindow() {
  const win = new BrowserWindow({
    kiosk: true,
    fullscreen: true,
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
    },
  });

  win.setMenuBarVisibility(false);
  // The kiosk shell only ever shows the OpenFlight UI itself; deny any
  // attempt (e.g. target="_blank" links) to pop a second window.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  attachRecovery(win.webContents, targetUrl);
  win.loadURL(targetUrl);

  win.on('closed', () => {
    app.quit();
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
