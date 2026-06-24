// Preload runs before the renderer's HTML. With contextIsolation off the
// preload and the renderer share the same window object, so we just expose
// the IPC helpers we need on window.pokerCoach.

const { ipcRenderer } = require('electron');

window.pokerCoach = {
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (s) => ipcRenderer.invoke('settings:set', s),
  captureRegion: (webContentsId, rect) =>
    ipcRenderer.invoke('capture:region', { webContentsId, rect }),
  getAppInfo: () => ipcRenderer.invoke('app:info')
};
