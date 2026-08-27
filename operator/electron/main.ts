import { app, BrowserWindow, ipcMain, Menu, protocol } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { GatewayInputCommand, PublicGatewayError } from "../shared/gateway-contract.js";
import { readBearerFromStdin } from "./bootstrap-auth.js";
import { GatewayClient, GatewayClientError } from "./gateway-client.js";
import { releaseOwnedLeaseBestEffort } from "./release-ownership.js";
import { developmentRendererUrl, isTrustedIpcSource } from "./renderer-policy.js";

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
const gatewayUrl = process.env.NOOB_GATEWAY_URL?.trim() || "http://127.0.0.1:18765";
const gatewayLabel = process.env.NOOB_GATEWAY_LABEL?.trim() || "uConsole · 192.0.2.83";
const gateway = new GatewayClient(gatewayUrl);
const bootstrapAuthenticationFromStdin = process.argv.includes("--auth-stdin");
const RELEASE_DEADLINE_MS = 2_000;
let safeToQuit = false;
let quitReleaseInFlight: Promise<void> | null = null;
let trustedOperatorWindow: BrowserWindow | null = null;

function publicFailure(error: unknown): never {
  const payload: PublicGatewayError =
    error instanceof GatewayClientError
      ? error.publicError
      : { code: "operator_internal_error", status: null };
  throw new Error(JSON.stringify(payload));
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
  trustedHandle("noob:get-config", () => ({
    gatewayUrl: gateway.baseUrl,
    gatewayLabel,
    streamUrl: "noob://gateway/stream",
    tokenConfigured: gateway.tokenConfigured,
  }));

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

  trustedHandle("noob:frame", async () => {
    try {
      return await gateway.frame();
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

app.whenReady().then(async () => {
  if (bootstrapAuthenticationFromStdin) {
    try {
      gateway.setToken(await readBearerFromStdin(process.stdin));
    } catch {
      gateway.clearToken();
    }
  }
  Menu.setApplicationMenu(null);
  registerIpc();
  protocol.handle("noob", async (request) => {
    const url = new URL(request.url);
    if (url.hostname !== "gateway" || url.pathname !== "/stream") {
      return new Response("not found", { status: 404 });
    }
    try {
      return await gateway.stream(request.signal);
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

app.on("before-quit", (event) => {
  if (safeToQuit) return;
  event.preventDefault();
  if (quitReleaseInFlight !== null) return;
  quitReleaseInFlight = releaseWithDeadline(null, "app-quit").finally(() => {
    safeToQuit = true;
    app.quit();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
