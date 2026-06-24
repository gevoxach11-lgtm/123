/*
 * Poker Coach - main Electron process.
 *
 * Hosts the application shell (a side coaching panel) and an embedded
 * <webview> that loads the user's poker site. Card recognition runs in
 * the renderer; this file exposes the few privileged APIs the renderer
 * needs (capture a region of a BrowserView/webview, persist settings).
 */

const { app, BrowserWindow, ipcMain, session, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

const SETTINGS_FILE = path.join(app.getPath('userData'), 'poker-coach-settings.json');

function loadSettings() {
  try {
    if (fs.existsSync(SETTINGS_FILE)) {
      return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf-8'));
    }
  } catch (err) {
    console.error('Failed to load settings:', err);
  }
  return {};
}

function saveSettings(settings) {
  try {
    fs.mkdirSync(path.dirname(SETTINGS_FILE), { recursive: true });
    fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2), 'utf-8');
    return true;
  } catch (err) {
    console.error('Failed to save settings:', err);
    return false;
  }
}

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    backgroundColor: '#0f1419',
    title: 'Poker Coach',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // Our main window loads only local trusted HTML; the user's site is
      // confined to the <webview> tag (separate process). Enabling node
      // integration here lets the analyzer load pokersolver directly.
      contextIsolation: false,
      nodeIntegration: true,
      webviewTag: true,
      sandbox: false
    }
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  // Open links that try to spawn new windows in the same webview.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  // Give the embedded site a realistic UA so it does not block us as a bot.
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    callback({ requestHeaders: details.requestHeaders });
  });

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------

ipcMain.handle('settings:get', () => loadSettings());

ipcMain.handle('settings:set', (_evt, settings) => {
  const merged = { ...loadSettings(), ...settings };
  saveSettings(merged);
  return merged;
});

/**
 * Capture a sub-rectangle from a webContents (the embedded webview).
 * Returns a PNG data URL.
 *
 * Args: { webContentsId, rect: { x, y, width, height } }
 *   - x, y, width, height are in webview-content coordinates (CSS pixels).
 *   - If rect is null/undefined we capture the whole webview.
 */
ipcMain.handle('capture:region', async (_evt, { webContentsId, rect }) => {
  const wc = require('electron').webContents.fromId(webContentsId);
  if (!wc) throw new Error(`webContents ${webContentsId} not found`);

  const image = rect
    ? await wc.capturePage({
        x: Math.max(0, Math.floor(rect.x)),
        y: Math.max(0, Math.floor(rect.y)),
        width: Math.max(1, Math.floor(rect.width)),
        height: Math.max(1, Math.floor(rect.height))
      })
    : await wc.capturePage();

  return image.toDataURL();
});

ipcMain.handle('app:info', () => ({
  platform: process.platform,
  version: app.getVersion(),
  tmp: os.tmpdir()
}));
