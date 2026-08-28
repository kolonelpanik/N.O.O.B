import { app, BrowserWindow, ipcMain, Menu, protocol } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { GatewayInputCommand, PublicGatewayError } from "../shared/gateway-contract.js";
import { readBearerFromStdin } from "./bootstrap-auth.js";
import { DeviceManager, type DeviceConnection } from "./device-manager.js";
import { GatewayClient, GatewayClientError } from "./gateway-client.js";
import { applyManagedTokenBestEffort } from "./managed-token.js";
import { releaseOwnedLeaseBestEffort } from "./release-ownership.js";
import { developmentRendererUrl, isTrustedIpcSource } from "./renderer-policy.js";
import { operatorSupportDirectory } from "./runtime-paths.js";
import { installSingleInstanceGuard } from "./single-instance.js";

protocol.registerSchemesAsPrivileged([
  {
    scheme: "noob",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      stream: true,
      corsEnabled: true,
    },
  },
]);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const initialGatewayUrl = process.env.NOOB_GATEWAY_URL?.trim() || "http://127.0.0.1:18765";
let gatewayLabel = process.env.NOOB_GATEWAY_LABEL?.trim() || "uConsole · 192.0.2.83";
let gateway = new GatewayClient(initialGatewayUrl);
let gatewayConnectionMode: "fixed" | "ssh-tunnel" = "fixed";
let deviceManager: DeviceManager | null = null;
let managedTokenFile: string | null = null;
const bootstrapAuthenticationFromStdin = process.argv.includes("--auth-stdin");
const RELEASE_DEADLINE_MS = 2_000;
let safeToQuit = false;
let quitReleaseInFlight: Promise<void> | null = null;
let trustedOperatorWindow: BrowserWindow | null = null;
const primaryInstance = installSingleInstanceGuard(
  app,
  () => trustedOperatorWindow,
);

function publicFailure(error: unknown): never {
  const payload: PublicGatewayError =
    error instanceof GatewayClientError
      ? error.publicError
      : error instanceof Error && /^[a-z0-9_]{3,80}$/.test(error.message)
        ? { code: error.message, status: null }
        : { code: "operator_internal_error", status: null };
  throw new Error(JSON.stringify(payload));
}

function manager(): DeviceManager {
  if (deviceManager === null) throw new Error("device_manager_unavailable");
  return deviceManager;
}

function currentConfig() {
  return {
    gatewayUrl: gateway.baseUrl,
    gatewayLabel,
    streamUrl: "noob://gateway/stream",
    tokenConfigured: gateway.tokenConfigured,
    connectionMode: gatewayConnectionMode,
    currentDeviceId: manager().currentDeviceId,
  } as const;
}

async function adoptDeviceConnection(connection: DeviceConnection) {
  gateway = new GatewayClient(connection.gatewayUrl);
  gatewayLabel = connection.device.profileName;
  gatewayConnectionMode = "ssh-tunnel";
  if (!bootstrapAuthenticationFromStdin && managedTokenFile !== null) {
    await applyManagedTokenBestEffort(gateway, managedTokenFile);
  }
  return { config: currentConfig(), device: connection.device };
}

function trustedHandle<TArgs extends unknown[], TResult>(
  channel: string,
  handler: (event: IpcMainInvokeEvent, ...args: TArgs) => TResult,
): void {
  ipcMain.handle(channel, (event, ...args) => {
    const trustedWebContents = trustedOperatorWindow?.webContents ?? null;
    if (!isTrustedIpcSource(
      trustedWebContents,
      event.sender,
      event.senderFrame,
      event.sender.mainFrame,
    )) {
      publicFailure(new GatewayClientError("untrusted_ipc_source"));
    }
    return handler(event, ...(args as TArgs));
  });
}

async function releaseOwnedBestEffort(window: BrowserWindow | null, reason: string): Promise<void> {
  try {
    await releaseOwnedLeaseBestEffort(gateway);
  } finally {
    if (window !== null && !window.isDestroyed()) {
      window.webContents.send("noob:control-lost", reason);
    }
  }
}

async function releaseWithDeadline(window: BrowserWindow | null, reason: string): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      releaseOwnedBestEffort(window, reason),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, RELEASE_DEADLINE_MS);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function registerIpc(): void {
  trustedHandle("noob:get-config", () => currentConfig());

  trustedHandle("noob:list-devices", async () => {
    try {
      return await manager().listDevices();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:discover-devices", async (_event, timeoutMs: unknown) => {
    if (typeof timeoutMs !== "number") publicFailure(new Error("invalid_discovery_timeout"));
    try {
      return await manager().discover(timeoutMs);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:probe-device", async (_event, address: unknown, sshPort: unknown) => {
    if (typeof address !== "string" || typeof sshPort !== "number") {
      publicFailure(new Error("invalid_device_probe"));
    }
    try {
      return await manager().probe(address, sshPort);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:inspect-device", async (_event, candidateId: unknown) => {
    if (typeof candidateId !== "string") publicFailure(new Error("invalid_candidate_id"));
    try {
      return await manager().inspect(candidateId);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:pair-connect-device", async (
    _event,
    candidateId: unknown,
    expectedFingerprint: unknown,
    profileName: unknown,
  ) => {
    if (typeof candidateId !== "string" || typeof expectedFingerprint !== "string" || typeof profileName !== "string") {
      publicFailure(new Error("invalid_device_pair_request"));
    }
    await releaseOwnedBestEffort(trustedOperatorWindow, "device-switch");
    gateway.clearToken();
    try {
      return await adoptDeviceConnection(
        await manager().pairAndConnect(candidateId, expectedFingerprint, profileName),
      );
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:connect-known-device", async (_event, deviceId: unknown) => {
    if (typeof deviceId !== "string") publicFailure(new Error("invalid_device_id"));
    await releaseOwnedBestEffort(trustedOperatorWindow, "device-switch");
    gateway.clearToken();
    try {
      return await adoptDeviceConnection(await manager().connectKnown(deviceId));
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:bootstrap-token", async (_event, token: unknown) => {
    if (typeof token !== "string") {
      publicFailure(new GatewayClientError("invalid_token"));
    }
    try {
      gateway.setToken(token);
      return await gateway.status();
    } catch (error) {
      gateway.clearToken();
      publicFailure(error);
    }
  });

  trustedHandle("noob:clear-token", async () => {
    await releaseOwnedBestEffort(null, "token-cleared");
    gateway.clearToken();
  });

  trustedHandle("noob:status", async () => {
    try {
      return await gateway.status();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:frame", async (_event, source: unknown = "target") => {
    if (source !== "target" && source !== "environment") {
      publicFailure(new GatewayClientError("invalid_frame_source"));
    }
    try {
      return await gateway.frame(source);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:video-modes", async () => {
    try {
      return await gateway.videoModes();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle(
    "noob:video-mode-set",
    async (_event, modeId: unknown, expectedGeneration: unknown) => {
      if (typeof modeId !== "string" || typeof expectedGeneration !== "number") {
        publicFailure(new GatewayClientError("invalid_video_mode_request"));
      }
      try {
        return await gateway.setVideoMode(modeId, expectedGeneration);
      } catch (error) {
        publicFailure(error);
      }
    },
  );

  trustedHandle("noob:claim", async () => {
    try {
      return await gateway.claim();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:renew", async () => {
    try {
      return await gateway.renew();
    } catch (error) {
      gateway.clearLease();
      publicFailure(error);
    }
  });

  trustedHandle("noob:release", async () => {
    try {
      return await gateway.release();
    } catch (error) {
      gateway.clearLease();
      publicFailure(error);
    }
  });

  trustedHandle("noob:input", async (_event, command: GatewayInputCommand) => {
    try {
      return await gateway.input(command);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:release-all", async () => {
    if (safeToQuit || quitReleaseInFlight !== null) {
      gateway.clearLease();
      return { ok: true, released: false };
    }
    try {
      return await gateway.releaseAll();
    } catch (error) {
      gateway.clearLease();
      publicFailure(error);
    }
  });

  trustedHandle("noob:local-input-arm", async () => {
    try {
      return await gateway.armLocalInput();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:local-input-disarm", async () => {
    try {
      return await gateway.disarmLocalInput();
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-state", async (_event, enabled: unknown, expectedGeneration: unknown) => {
    if (typeof enabled !== "boolean" || typeof expectedGeneration !== "number") {
      publicFailure(new GatewayClientError("invalid_environment_camera_request"));
    }
    try {
      return await gateway.setEnvironmentCamera(enabled, expectedGeneration);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-snapshot", async (_event, expectedGeneration: unknown) => {
    if (typeof expectedGeneration !== "number") {
      publicFailure(new GatewayClientError("invalid_environment_camera_request"));
    }
    try {
      return await gateway.captureEnvironmentSnapshot(expectedGeneration);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-media", async (_event, limit: unknown, cursor: unknown) => {
    if (typeof limit !== "number" || (cursor !== undefined && typeof cursor !== "string")) {
      publicFailure(new GatewayClientError("invalid_environment_media_request"));
    }
    try {
      return await gateway.listEnvironmentMedia(limit, cursor);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-clip-start", async (
    _event,
    durationSeconds: unknown,
    fps: unknown,
    expectedGeneration: unknown,
  ) => {
    if (typeof durationSeconds !== "number" || typeof fps !== "number" || typeof expectedGeneration !== "number") {
      publicFailure(new GatewayClientError("invalid_environment_clip_request"));
    }
    try {
      return await gateway.startEnvironmentClip(durationSeconds, fps, expectedGeneration);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-clip-status", async (_event, jobId: unknown) => {
    if (typeof jobId !== "string") publicFailure(new GatewayClientError("invalid_environment_clip_job"));
    try {
      return await gateway.getEnvironmentClipJob(jobId);
    } catch (error) {
      publicFailure(error);
    }
  });

  trustedHandle("noob:environment-camera-clip-stop", async (_event, jobId: unknown) => {
    if (typeof jobId !== "string") publicFailure(new GatewayClientError("invalid_environment_clip_job"));
    try {
      return await gateway.stopEnvironmentClip(jobId);
    } catch (error) {
      publicFailure(error);
    }
  });
}

async function createWindow(): Promise<BrowserWindow> {
  const preload = path.join(__dirname, "preload.cjs");
  const window = new BrowserWindow({
    width: 1586,
    height: 992,
    minWidth: 920,
    minHeight: 680,
    backgroundColor: "#080d11",
    show: false,
    title: "N.O.O.B",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "hidden",
    trafficLightPosition: { x: 20, y: 19 },
    webPreferences: {
      preload,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  trustedOperatorWindow = window;

  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  let closeAllowed = false;
  let closeReleaseInFlight: Promise<void> | null = null;
  window.webContents.on("render-process-gone", () => {
    if (!safeToQuit && quitReleaseInFlight === null && closeReleaseInFlight === null) {
      void releaseOwnedBestEffort(null, "renderer-gone");
    }
  });
  window.on("blur", () => {
    if (!safeToQuit && quitReleaseInFlight === null && closeReleaseInFlight === null) {
      void releaseOwnedBestEffort(window, "window-blur");
    }
  });
  window.on("close", (event) => {
    if (safeToQuit || closeAllowed) return;
    event.preventDefault();
    if (closeReleaseInFlight !== null) return;
    closeReleaseInFlight = releaseWithDeadline(null, "window-close").finally(() => {
      closeAllowed = true;
      if (!window.isDestroyed()) window.close();
    });
  });
  window.on("closed", () => {
    if (trustedOperatorWindow === window) trustedOperatorWindow = null;
  });
  window.once("ready-to-show", () => window.show());

  const rendererUrl = developmentRendererUrl(process.env.NOOB_RENDERER_URL, app.isPackaged);
  if (rendererUrl !== null) {
    await window.loadURL(rendererUrl);
  } else {
    await window.loadFile(path.join(__dirname, "../../dist/index.html"));
  }
  return window;
}

if (primaryInstance) app.whenReady().then(async () => {
  const supportDir = operatorSupportDirectory({
    configured: process.env.NOOB_OPERATOR_SUPPORT_DIR,
  });
  managedTokenFile = path.join(supportDir, "gateway.token");
  deviceManager = new DeviceManager({
    supportDir,
    identityFile: process.env.NOOB_SSH_IDENTITY_FILE?.trim() || undefined,
    sshUser: process.env.NOOB_SSH_USER?.trim() || undefined,
    remoteGatewayPort: process.env.NOOB_REMOTE_GATEWAY_PORT === undefined
      ? undefined
      : Number(process.env.NOOB_REMOTE_GATEWAY_PORT),
  });
  try {
    const preferred = await manager().connectDefault();
    if (preferred !== null) await adoptDeviceConnection(preferred);
  } catch {
    // A stale address, unavailable appliance, missing identity, or changed key
    // remains an explicit disconnected state; startup never weakens trust.
  }
  if (bootstrapAuthenticationFromStdin) {
    try {
      gateway.setToken(await readBearerFromStdin(process.stdin));
    } catch {
      gateway.clearToken();
    }
  } else {
    await applyManagedTokenBestEffort(gateway, managedTokenFile);
  }
  Menu.setApplicationMenu(null);
  registerIpc();
  protocol.handle("noob", async (request) => {
    const url = new URL(request.url);
    if (url.hostname !== "gateway") {
      return new Response("not found", { status: 404 });
    }
    try {
      if (url.pathname === "/stream") return await gateway.stream(request.signal);
      if (url.pathname === "/environment-stream") return await gateway.environmentStream(request.signal);
      const mediaMatch = url.pathname.match(/^\/environment-media\/(m_[0-9a-f]{32})$/);
      if (mediaMatch?.[1]) return await gateway.environmentMediaContent(mediaMatch[1], request.signal);
      const mediaFrameMatch = url.pathname.match(
        /^\/environment-media\/(m_[0-9a-f]{32})\/frames\/(0|[1-9][0-9]?|1[0-4][0-9])$/,
      );
      if (mediaFrameMatch?.[1] && mediaFrameMatch[2]) {
        return await gateway.environmentMediaFrame(
          mediaFrameMatch[1],
          Number.parseInt(mediaFrameMatch[2], 10),
          request.signal,
        );
      }
      return new Response("not found", { status: 404 });
    } catch (error) {
      const status = error instanceof GatewayClientError ? error.publicError.status ?? 503 : 503;
      return new Response("stream unavailable", {
        status,
        headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
      });
    }
  });
  await createWindow();

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createWindow();
    }
  });
});

if (primaryInstance) app.on("before-quit", (event) => {
  if (safeToQuit) return;
  event.preventDefault();
  if (quitReleaseInFlight !== null) return;
  quitReleaseInFlight = (async () => {
    try {
      await releaseWithDeadline(null, "app-quit");
      await manager().close();
    } finally {
      safeToQuit = true;
      app.quit();
    }
  })();
});

if (primaryInstance) app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
