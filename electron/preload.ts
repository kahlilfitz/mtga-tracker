import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("api", {
  reparse: () => ipcRenderer.invoke("reparse"),
});
