import { contextBridge, ipcRenderer } from "electron";
import type { GatewayInputCommand, NoobBridge } from "../shared/gateway-contract.js";

const bridge: NoobBridge = {
  getConfig: () => ipcRenderer.invoke("noob:get-config"),
  listDevices: () => ipcRenderer.invoke("noob:list-devices"),
  discoverDevices: (timeoutMs) => ipcRenderer.invoke("noob:discover-devices", timeoutMs),
  probeDevice: (address, sshPort) => ipcRenderer.invoke("noob:probe-device", address, sshPort),
  inspectDevice: (candidateId) => ipcRenderer.invoke("noob:inspect-device", candidateId),
  pairAndConnectDevice: (candidateId, expectedFingerprint, profileName) =>
    ipcRenderer.invoke("noob:pair-connect-device", candidateId, expectedFingerprint, profileName),
  connectKnownDevice: (deviceId) => ipcRenderer.invoke("noob:connect-known-device", deviceId),
  bootstrapToken: (token: string) => ipcRenderer.invoke("noob:bootstrap-token", token),
  clearToken: () => ipcRenderer.invoke("noob:clear-token"),
  getStatus: () => ipcRenderer.invoke("noob:status"),
  getFrame: (source = "target") => ipcRenderer.invoke("noob:frame", source),
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
  setEnvironmentCamera: (enabled, expectedGeneration) =>
    ipcRenderer.invoke("noob:environment-camera-state", enabled, expectedGeneration),
  captureEnvironmentSnapshot: (expectedGeneration) =>
    ipcRenderer.invoke("noob:environment-camera-snapshot", expectedGeneration),
  listEnvironmentMedia: (limit, cursor) =>
    ipcRenderer.invoke("noob:environment-camera-media", limit, cursor),
  startEnvironmentClip: (durationSeconds, fps, expectedGeneration) =>
    ipcRenderer.invoke("noob:environment-camera-clip-start", durationSeconds, fps, expectedGeneration),
  getEnvironmentClipJob: (jobId) => ipcRenderer.invoke("noob:environment-camera-clip-status", jobId),
  stopEnvironmentClip: (jobId) => ipcRenderer.invoke("noob:environment-camera-clip-stop", jobId),
  onControlLost: (listener: (reason: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, reason: string) => listener(reason);
    ipcRenderer.on("noob:control-lost", handler);
    return () => ipcRenderer.removeListener("noob:control-lost", handler);
  },
};

contextBridge.exposeInMainWorld("noob", bridge);
