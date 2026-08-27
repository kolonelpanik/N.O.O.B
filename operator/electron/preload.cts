import { contextBridge, ipcRenderer } from "electron";
import type { GatewayInputCommand, NoobBridge } from "../shared/gateway-contract.js";

const bridge: NoobBridge = {
  getConfig: () => ipcRenderer.invoke("noob:get-config"),
  bootstrapToken: (token: string) => ipcRenderer.invoke("noob:bootstrap-token", token),
  clearToken: () => ipcRenderer.invoke("noob:clear-token"),
  getStatus: () => ipcRenderer.invoke("noob:status"),
  getFrame: () => ipcRenderer.invoke("noob:frame"),
  getVideoModes: () => ipcRenderer.invoke("noob:video-modes"),
  setVideoMode: (modeId: string, expectedGeneration: number) =>
    ipcRenderer.invoke("noob:video-mode-set", modeId, expectedGeneration),
  claimControl: () => ipcRenderer.invoke("noob:claim"),
  renewControl: () => ipcRenderer.invoke("noob:renew"),
  releaseControl: () => ipcRenderer.invoke("noob:release"),
  sendInput: (command: GatewayInputCommand) => ipcRenderer.invoke("noob:input", command),
  releaseAll: () => ipcRenderer.invoke("noob:release-all"),
  armLocalInput: () => ipcRenderer.invoke("noob:local-input-arm"),
  disarmLocalInput: () => ipcRenderer.invoke("noob:local-input-disarm"),
  onControlLost: (listener: (reason: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, reason: string) => listener(reason);
    ipcRenderer.on("noob:control-lost", handler);
    return () => ipcRenderer.removeListener("noob:control-lost", handler);
  },
};

contextBridge.exposeInMainWorld("noob", bridge);
