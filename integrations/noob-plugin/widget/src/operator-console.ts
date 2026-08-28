import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import "./operator-console.css";

type SourceId = "target" | "environment";

interface OpenState {
  device_id?: string;
  initial_source_id?: SourceId;
}

interface FrameState {
  device_id?: string;
  source_id?: SourceId;
  frame_token?: string;
  sequence?: number;
  generation?: number;
}

interface OpenAiCompat {
  requestDisplayMode?(options: { mode: "inline" | "pip" | "fullscreen" }): Promise<unknown>;
}

declare global {
  interface Window { openai?: OpenAiCompat }
}

const frame = document.getElementById("frame") as HTMLImageElement;
const empty = document.getElementById("empty")!;
const proof = document.getElementById("proof")!;
const gatewayState = document.getElementById("gateway-state")!;
const frameState = document.getElementById("frame-state")!;
const cameraState = document.getElementById("camera-state")!;
const storageState = document.getElementById("storage-state")!;
const sourceBadge = document.getElementById("source-badge")!;
const cameraToggle = document.getElementById("camera-toggle") as HTMLButtonElement;
const screenshot = document.getElementById("screenshot") as HTMLButtonElement;
const message = document.getElementById("message")!;
const device = document.getElementById("device")!;
const zoomLabel = document.getElementById("zoom-label")!;

const app = new App({ name: "N.O.O.B. Operator", version: "0.2.0" });
let deviceId = "";
let source: SourceId = "target";
let currentFrameToken = "";
let generation = 0;
let cameraEnabled = false;
let zoom = 0;
let polling = false;

function hostContext(context: NonNullable<ReturnType<App["getHostContext"]>>): void {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
}

function structured(result: CallToolResult): Record<string, unknown> {
  return (result.structuredContent ?? {}) as Record<string, unknown>;
}

function imageData(result: CallToolResult): string | null {
  const image = result.content?.find((item) => item.type === "image");
  return image?.type === "image" ? `data:${image.mimeType};base64,${image.data}` : null;
}

function setMessage(value: string, failure = false): void {
  message.textContent = value;
  message.classList.toggle("message--error", failure);
}

function applyZoom(): void {
  const labels = ["FIT", "100%", "150%", "200%"];
  zoomLabel.textContent = labels[zoom] ?? "FIT";
  frame.className = zoom === 0 ? "" : `frame--zoom-${zoom}`;
}

function selectSource(next: SourceId): void {
  source = next;
  currentFrameToken = "";
  document.querySelectorAll<HTMLButtonElement>(".tab").forEach((button) => {
    button.classList.toggle("tab--active", button.dataset.source === source);
  });
  sourceBadge.textContent = source === "target" ? "TARGET HDMI" : "ENVIRONMENT CAMERA";
  cameraToggle.disabled = source !== "environment" || !deviceId;
  screenshot.disabled = true;
  void refreshFrame();
}

async function refreshStatus(): Promise<void> {
  if (!deviceId) return;
  try {
    const result = await app.callServerTool({ name: "noob_get_status", arguments: { device_id: deviceId } });
    const root = structured(result);
    const status = (root.status ?? {}) as Record<string, unknown>;
    const environment = (status.environment_camera ?? {}) as Record<string, unknown>;
    const storage = (environment.storage ?? {}) as Record<string, unknown>;
    gatewayState.textContent = status.ok === true ? "READY" : "CONNECTED";
    cameraEnabled = environment.stream_enabled === true || environment.enabled === true;
    generation = typeof environment.generation === "number" ? environment.generation : generation;
    cameraState.textContent = environment.configured === false ? "NOT CONFIGURED" : cameraEnabled ? "STREAM ON" : "STREAM OFF";
    storageState.textContent = storage.mounted === true ? "MOUNTED" : environment.configured === true ? "DEGRADED" : "OPTIONAL";
    cameraToggle.textContent = cameraEnabled ? "TURN CAMERA OFF" : "TURN CAMERA ON";
    proof.textContent = status.control && typeof status.control === "object" && (status.control as Record<string, unknown>).active === true ? "CONTROL ACTIVE" : "OBSERVE ONLY";
  } catch {
    gatewayState.textContent = "DEGRADED";
  }
}

async function refreshFrame(): Promise<void> {
  if (!deviceId || polling) return;
  polling = true;
  try {
    const result = await app.callServerTool({ name: "noob_widget_poll_frame", arguments: { device_id: deviceId, source_id: source } });
    if (result.isError) throw new Error("frame unavailable");
    const data = imageData(result);
    const state = structured(result) as FrameState;
    if (!data) throw new Error("frame unavailable");
    frame.src = data;
    frame.hidden = false;
    empty.hidden = true;
    currentFrameToken = state.frame_token ?? "";
    generation = state.generation ?? generation;
    frameState.textContent = `LIVE · ${state.sequence ?? "—"}`;
    screenshot.disabled = !currentFrameToken;
  } catch {
    frameState.textContent = "UNAVAILABLE";
    currentFrameToken = "";
    screenshot.disabled = true;
  } finally {
    polling = false;
  }
}

app.ontoolresult = (result) => {
  const state = structured(result) as OpenState;
  if (state.device_id) {
    deviceId = state.device_id;
    device.textContent = deviceId;
    selectSource(state.initial_source_id ?? source);
    void refreshStatus();
  }
};
app.onhostcontextchanged = hostContext;

document.querySelectorAll<HTMLButtonElement>(".tab").forEach((button) => {
  button.addEventListener("click", () => selectSource(button.dataset.source as SourceId));
});
document.getElementById("refresh")!.addEventListener("click", () => void refreshFrame());
document.getElementById("zoom-out")!.addEventListener("click", () => { zoom = Math.max(0, zoom - 1); applyZoom(); });
document.getElementById("zoom-in")!.addEventListener("click", () => { zoom = Math.min(3, zoom + 1); applyZoom(); });
zoomLabel.addEventListener("click", () => { zoom = 0; applyZoom(); });
document.getElementById("fullscreen")!.addEventListener("click", async () => {
  try {
    if (window.openai?.requestDisplayMode) await window.openai.requestDisplayMode({ mode: "fullscreen" });
    else await document.documentElement.requestFullscreen();
  } catch { setMessage("This host did not grant fullscreen", true); }
});
cameraToggle.addEventListener("click", async () => {
  if (!deviceId || source !== "environment") return;
  cameraToggle.disabled = true;
  try {
    const result = await app.callServerTool({
      name: "noob_set_camera_streaming",
      arguments: { device_id: deviceId, enabled: !cameraEnabled, expected_generation: generation, request_id: crypto.randomUUID() },
    });
    if (result.isError) throw new Error("camera state rejected");
    setMessage(!cameraEnabled ? "Camera stream requested on" : "Camera sensor/stream requested off; 5V remains present");
    await refreshStatus();
    await refreshFrame();
  } catch { setMessage("Camera state change was not confirmed", true); }
  finally { cameraToggle.disabled = false; }
});
screenshot.addEventListener("click", async () => {
  if (!deviceId || !currentFrameToken) return;
  screenshot.disabled = true;
  try {
    const result = await app.callServerTool({
      name: "noob_save_screenshot",
      arguments: { device_id: deviceId, source_id: source, expected_frame_token: currentFrameToken, request_id: crypto.randomUUID() },
    });
    if (result.isError) throw new Error("screenshot rejected");
    setMessage("Explicit screenshot stored; no automatic recording is active");
  } catch { setMessage("Screenshot was not stored", true); }
  finally { screenshot.disabled = !currentFrameToken; }
});
document.getElementById("release")!.addEventListener("click", async () => {
  if (!deviceId) return;
  try {
    await app.callServerTool({ name: "noob_emergency_release_all", arguments: { device_id: deviceId, reason: "widget emergency release", request_id: crypto.randomUUID() } });
    setMessage("Emergency input release completed");
    await refreshStatus();
  } catch { setMessage("Emergency release could not be confirmed", true); }
});

app.connect().then(() => {
  const context = app.getHostContext();
  if (context) hostContext(context);
  window.setInterval(() => {
    if (document.visibilityState === "visible") {
      void refreshStatus();
      void refreshFrame();
    }
  }, 1_000);
});
