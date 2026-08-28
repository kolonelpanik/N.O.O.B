import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import "./operator-console.css";

type SourceId = "target" | "environment";
type ViewTab = "live" | "storage" | "health";

interface OpenState {
  device_id?: string;
  initial_source_id?: SourceId;
  tab?: ViewTab;
  ui_version?: string;
}

interface FrameState {
  device_id?: string;
  source_id?: SourceId;
  frame_token?: string;
  sequence?: number;
  generation?: number;
}

interface FrameCapture {
  data: string;
  state: FrameState;
}

interface DeviceContext {
  deviceId: string;
  epoch: number;
}

interface MediaItem {
  id: string;
  kind: "snapshot" | "clip";
  created_at: string | null;
  size_bytes: number;
  width: number;
  height: number;
  frame_count: number;
  duration_ms: number;
}

interface OpenAiCompat {
  requestDisplayMode?(options: { mode: "inline" | "pip" | "fullscreen" }): Promise<unknown>;
}

declare global {
  interface Window { openai?: OpenAiCompat }
}

const MEDIA_ID = /^m_[0-9a-f]{32}$/;

function element<T extends HTMLElement>(id: string): T {
  const value = document.getElementById(id);
  if (!value) throw new Error(`missing_widget_element_${id}`);
  return value as T;
}

const frame = element<HTMLImageElement>("frame");
const empty = element("empty");
const proof = element("proof");
const gatewayState = element("gateway-state");
const frameState = element("frame-state");
const cameraState = element("camera-state");
const storageState = element("storage-state");
const sourceBadge = element("source-badge");
const cameraToggle = element<HTMLButtonElement>("camera-toggle");
const screenshot = element<HTMLButtonElement>("screenshot");
const message = element("message");
const device = element("device");
const zoomLabel = element("zoom-label");
const liveView = element<HTMLElement>("live-view");
const storageView = element<HTMLElement>("storage-view");
const healthView = element<HTMLElement>("health-view");
const mediaEmpty = element("media-empty");
const mediaList = element("media-list");
const mediaMore = element<HTMLButtonElement>("media-more");
const mediaPreview = element<HTMLElement>("media-preview");
const previewKind = element("preview-kind");
const previewId = element("preview-id");
const previewImage = element<HTMLImageElement>("preview-image");
const previewMeta = element("preview-meta");
const previewControls = element<HTMLElement>("preview-controls");
const previewPrevious = element<HTMLButtonElement>("preview-previous");
const previewNext = element<HTMLButtonElement>("preview-next");
const previewFrame = element("preview-frame");

const app = new App({ name: "N.O.O.B. Operator", version: "0.2.0" });
let deviceId = "";
let source: SourceId = "target";
let activeView: ViewTab = "live";
let currentFrameToken = "";
let generation = 0;
let cameraEnabled = false;
let zoom = 0;
let mediaItems: MediaItem[] = [];
let nextMediaCursor: string | null = null;
let selectedMedia: MediaItem | null = null;
let selectedFrameIndex = 0;
let contextEpoch = 0;
let statusRequestSerial = 0;
let mediaRequestSerial = 0;
let previewRequestSerial = 0;
const frameRequests = new Map<string, Promise<FrameCapture | null>>();

function hostContext(context: NonNullable<ReturnType<App["getHostContext"]>>): void {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
}

function structured(result: CallToolResult): Record<string, unknown> {
  return (result.structuredContent ?? {}) as Record<string, unknown>;
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function imageData(result: CallToolResult): string | null {
  const image = result.content?.find((item) => item.type === "image");
  return image?.type === "image" && image.mimeType === "image/jpeg"
    ? `data:image/jpeg;base64,${image.data}`
    : null;
}

function setMessage(value: string, failure = false): void {
  message.textContent = value;
  message.classList.toggle("message--error", failure);
}

function setHealth(id: string, value: string, posture: "good" | "warn" | "bad" = "good"): void {
  const node = element(id);
  node.textContent = value;
  node.classList.toggle("proof--warn", posture === "warn");
  node.classList.toggle("proof--bad", posture === "bad");
}

function currentDeviceContext(): DeviceContext | null {
  return deviceId ? { deviceId, epoch: contextEpoch } : null;
}

function contextIsCurrent(context: DeviceContext): boolean {
  return context.epoch === contextEpoch && context.deviceId === deviceId;
}

function resultMatchesDevice(result: CallToolResult, context: DeviceContext): boolean {
  return structured(result).device_id === context.deviceId;
}

function frameRequestKey(context: DeviceContext, requestedSource: SourceId): string {
  return `${context.epoch}:${context.deviceId}:${requestedSource}`;
}

function resetDeviceBoundState(nextDeviceId: string): void {
  contextEpoch += 1;
  statusRequestSerial += 1;
  mediaRequestSerial += 1;
  previewRequestSerial += 1;
  deviceId = nextDeviceId;
  currentFrameToken = "";
  generation = 0;
  cameraEnabled = false;
  mediaItems = [];
  nextMediaCursor = null;
  selectedMedia = null;
  selectedFrameIndex = 0;

  device.textContent = deviceId;
  frame.removeAttribute("src");
  frame.hidden = true;
  empty.hidden = false;
  empty.textContent = "LOADING PAIRED DEVICE";
  gatewayState.textContent = "—";
  frameState.textContent = "—";
  cameraState.textContent = "OPTIONAL";
  storageState.textContent = "—";
  proof.textContent = "OBSERVE ONLY";
  cameraToggle.textContent = "CAMERA STREAM";
  cameraToggle.disabled = true;
  previewImage.removeAttribute("src");
  mediaPreview.hidden = true;
  renderMedia();
  for (const healthId of [
    "health-gateway",
    "health-video",
    "health-serial",
    "health-control",
    "health-camera",
    "health-storage",
  ]) setHealth(healthId, "—");
  setMessage("Loading verified state for the selected appliance");
}

function applyZoom(): void {
  const labels = ["FIT", "100%", "150%", "200%"];
  zoomLabel.textContent = labels[zoom] ?? "FIT";
  frame.className = zoom === 0 ? "" : `frame--zoom-${zoom}`;
}

function updateScreenshotAction(): void {
  screenshot.textContent = source === "target"
    ? "DOWNLOAD FRESH TARGET JPEG"
    : "STORE CAMERA SNAPSHOT";
  screenshot.disabled = !deviceId;
}

function selectSource(next: SourceId, refresh = true): void {
  source = next;
  currentFrameToken = "";
  document.querySelectorAll<HTMLButtonElement>(".source-tab").forEach((button) => {
    const selected = button.dataset.source === source;
    button.classList.toggle("source-tab--active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  sourceBadge.textContent = source === "target" ? "TARGET HDMI" : "ENVIRONMENT CAMERA";
  cameraToggle.disabled = source !== "environment" || !deviceId;
  updateScreenshotAction();
  if (refresh && activeView === "live") void refreshFrame(source);
}

function selectView(next: ViewTab, refresh = true): void {
  activeView = next;
  liveView.hidden = next !== "live";
  storageView.hidden = next !== "storage";
  healthView.hidden = next !== "health";
  document.querySelectorAll<HTMLButtonElement>(".view-tab").forEach((button) => {
    const selected = button.dataset.view === next;
    button.classList.toggle("view-tab--active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  if (!refresh || !deviceId) return;
  if (next === "live") void refreshFrame(source);
  if (next === "storage") void refreshMedia(true);
  if (next === "health") void refreshStatus();
}

async function refreshStatus(context: DeviceContext | null = currentDeviceContext()): Promise<void> {
  if (!context) return;
  const requestSerial = ++statusRequestSerial;
  try {
    const result = await app.callServerTool({
      name: "noob_get_status",
      arguments: { device_id: context.deviceId },
    });
    if (!contextIsCurrent(context) || requestSerial !== statusRequestSerial) return;
    if (result.isError) throw new Error("status unavailable");
    if (!resultMatchesDevice(result, context)) throw new Error("status device mismatch");
    const root = structured(result);
    const status = object(root.status);
    const environment = object(status.environment_camera);
    const storage = object(environment.storage);
    const serial = object(status.serial);
    const video = object(status.video);
    const control = object(status.control);

    gatewayState.textContent = status.ok === true ? "READY" : "CONNECTED";
    cameraEnabled = environment.stream_enabled === true || environment.enabled === true;
    generation = typeof environment.generation === "number" ? environment.generation : generation;
    cameraState.textContent = environment.configured !== true
      ? "NOT CONFIGURED"
      : cameraEnabled ? "STREAM ON" : "STREAM OFF";
    storageState.textContent = storage.mounted === true
      ? storage.writable === false ? "READ ONLY" : "MOUNTED"
      : environment.configured === true ? "DEGRADED" : "OPTIONAL";
    cameraToggle.textContent = cameraEnabled ? "TURN CAMERA OFF" : "TURN CAMERA ON";
    proof.textContent = control.active === true ? "CONTROL ACTIVE" : "OBSERVE ONLY";

    setHealth("health-gateway", status.ok === true ? "READY" : "CONNECTED", status.ok === true ? "good" : "warn");
    setHealth("health-video", video.ready === true ? "READY" : "UNAVAILABLE", video.ready === true ? "good" : "bad");
    setHealth("health-serial", serial.ready === true ? "READY" : "UNAVAILABLE", serial.ready === true ? "good" : "bad");
    setHealth("health-control", control.active === true ? "CONTROL ACTIVE" : "OBSERVE ONLY", control.active === true ? "warn" : "good");

    if (environment.configured !== true) {
      setHealth("health-camera", "OPTIONAL / NOT CONFIGURED", "warn");
      setHealth("health-storage", "OPTIONAL", "warn");
    } else {
      const cameraReady = environment.frame_ready === true;
      const cameraReachable = environment.reachable === true;
      setHealth(
        "health-camera",
        cameraReady ? "FRAME READY" : cameraReachable ? "REACHABLE / IDLE" : "UNREACHABLE",
        cameraReady ? "good" : cameraReachable ? "warn" : "bad",
      );
      const mounted = storage.mounted === true;
      const writable = storage.writable === true;
      setHealth(
        "health-storage",
        mounted ? writable ? "MOUNTED / WRITABLE" : "MOUNTED / READ ONLY" : "UNAVAILABLE",
        mounted && writable ? "good" : mounted ? "warn" : "bad",
      );
    }
  } catch {
    if (!contextIsCurrent(context) || requestSerial !== statusRequestSerial) return;
    gatewayState.textContent = "DEGRADED";
    setHealth("health-gateway", "DEGRADED", "bad");
  }
}

function refreshFrame(
  requestedSource: SourceId = source,
  context: DeviceContext | null = currentDeviceContext(),
): Promise<FrameCapture | null> {
  if (!context) return Promise.resolve(null);
  const requestKey = frameRequestKey(context, requestedSource);
  const existing = frameRequests.get(requestKey);
  if (existing) return existing;
  const request = (async (): Promise<FrameCapture | null> => {
    try {
      const result = await app.callServerTool({
        name: "noob_widget_poll_frame",
        arguments: { device_id: context.deviceId, source_id: requestedSource },
      });
      if (!contextIsCurrent(context)) return null;
      if (result.isError) throw new Error("frame unavailable");
      const data = imageData(result);
      const state = structured(result) as FrameState;
      if (
        !data
        || state.device_id !== context.deviceId
        || state.source_id !== requestedSource
        || !state.frame_token
      ) {
        throw new Error("frame proof unavailable");
      }
      if (contextIsCurrent(context) && source === requestedSource) {
        frame.src = data;
        frame.hidden = false;
        empty.hidden = true;
        currentFrameToken = state.frame_token;
        generation = state.generation ?? generation;
        frameState.textContent = `LIVE · ${state.sequence ?? "—"}`;
        updateScreenshotAction();
      }
      return { data, state };
    } catch {
      if (contextIsCurrent(context) && source === requestedSource) {
        frameState.textContent = "UNAVAILABLE";
        currentFrameToken = "";
      }
      return null;
    }
  })();
  frameRequests.set(requestKey, request);
  void request.finally(() => {
    if (frameRequests.get(requestKey) === request) frameRequests.delete(requestKey);
  });
  return request;
}

function parseMediaItem(value: unknown): MediaItem | null {
  const item = object(value);
  if (
    typeof item.id !== "string"
    || !MEDIA_ID.test(item.id)
    || (item.kind !== "snapshot" && item.kind !== "clip")
    || item.state !== "complete"
    || !Number.isSafeInteger(item.frame_count)
    || (item.frame_count as number) < 1
    || (item.frame_count as number) > 150
  ) return null;
  const boundedNumber = (candidate: unknown, fallback = 0): number =>
    typeof candidate === "number" && Number.isFinite(candidate) && candidate >= 0 ? candidate : fallback;
  return {
    id: item.id,
    kind: item.kind,
    created_at: typeof item.created_at === "string" ? item.created_at : null,
    size_bytes: boundedNumber(item.size_bytes),
    width: boundedNumber(item.width),
    height: boundedNumber(item.height),
    frame_count: item.frame_count as number,
    duration_ms: boundedNumber(item.duration_ms),
  };
}

function shortMediaId(id: string): string {
  return `${id.slice(0, 10)}…${id.slice(-6)}`;
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

function formatMediaTime(value: string | null): string {
  if (value === null) return "CAMERA CLOCK UNAVAILABLE";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "CAMERA CLOCK UNAVAILABLE" : parsed.toLocaleString();
}

function renderMedia(): void {
  mediaList.replaceChildren();
  for (const item of mediaItems) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "media-item";
    button.dataset.mediaId = item.id;

    const title = document.createElement("strong");
    title.textContent = item.kind === "snapshot"
      ? "SNAPSHOT"
      : `${(item.duration_ms / 1_000).toFixed(1)}s CLIP · ${item.frame_count} FRAMES`;
    const id = document.createElement("span");
    id.textContent = shortMediaId(item.id);
    id.title = item.id;
    const time = document.createElement("small");
    time.textContent = formatMediaTime(item.created_at);
    const dimensions = document.createElement("small");
    dimensions.textContent = `${item.width} × ${item.height} · ${formatBytes(item.size_bytes)}`;
    button.append(title, id, time, dimensions);
    button.addEventListener("click", () => void openMedia(item, 0));
    mediaList.append(button);
  }
  mediaEmpty.hidden = mediaItems.length > 0;
  mediaEmpty.textContent = mediaItems.length > 0 ? "" : "NO COMPLETED CAMERA MEDIA";
  mediaMore.hidden = nextMediaCursor === null;
}

async function refreshMedia(
  reset: boolean,
  context: DeviceContext | null = currentDeviceContext(),
): Promise<void> {
  if (!context) return;
  const cursor = reset ? undefined : nextMediaCursor ?? undefined;
  if (!reset && !cursor) return;
  const requestSerial = ++mediaRequestSerial;
  const refreshButton = element<HTMLButtonElement>("storage-refresh");
  refreshButton.disabled = true;
  mediaMore.disabled = true;
  try {
    const result = await app.callServerTool({
      name: "noob_list_media",
      arguments: { device_id: context.deviceId, ...(cursor ? { cursor } : {}), limit: 20 },
    });
    if (!contextIsCurrent(context) || requestSerial !== mediaRequestSerial) return;
    if (result.isError) throw new Error("media unavailable");
    if (!resultMatchesDevice(result, context)) throw new Error("media device mismatch");
    const page = object(structured(result).media);
    const parsed = Array.isArray(page.items)
      ? page.items.map(parseMediaItem).filter((item): item is MediaItem => item !== null)
      : [];
    const combined = reset ? parsed : [...mediaItems, ...parsed];
    mediaItems = [...new Map(combined.map((item) => [item.id, item])).values()];
    nextMediaCursor = typeof page.next_cursor === "string" && MEDIA_ID.test(page.next_cursor)
      ? page.next_cursor
      : null;
    renderMedia();
    setMessage(`Loaded ${mediaItems.length} completed camera media item${mediaItems.length === 1 ? "" : "s"}`);
  } catch {
    if (!contextIsCurrent(context) || requestSerial !== mediaRequestSerial) return;
    if (reset) {
      mediaItems = [];
      nextMediaCursor = null;
      renderMedia();
    }
    mediaEmpty.hidden = false;
    mediaEmpty.textContent = "CAMERA STORAGE UNAVAILABLE";
    setMessage("Camera storage could not be read", true);
  } finally {
    if (!contextIsCurrent(context) || requestSerial !== mediaRequestSerial) return;
    refreshButton.disabled = false;
    mediaMore.disabled = false;
  }
}

async function openMedia(
  item: MediaItem,
  frameIndex: number,
  context: DeviceContext | null = currentDeviceContext(),
): Promise<void> {
  if (!context || !MEDIA_ID.test(item.id)) return;
  const boundedIndex = Math.max(0, Math.min(item.frame_count - 1, frameIndex));
  const requestSerial = ++previewRequestSerial;
  try {
    const result = item.kind === "snapshot"
      ? await app.callServerTool({
        name: "noob_get_media",
        arguments: { device_id: context.deviceId, media_id: item.id },
      })
      : await app.callServerTool({
        name: "noob_get_clip_frame",
        arguments: { device_id: context.deviceId, media_id: item.id, frame_index: boundedIndex },
      });
    if (!contextIsCurrent(context) || requestSerial !== previewRequestSerial) return;
    if (result.isError) throw new Error("preview unavailable");
    const state = structured(result);
    if (
      state.device_id !== context.deviceId
      || state.media_id !== item.id
      || (item.kind === "clip" && state.frame_index !== boundedIndex)
    ) throw new Error("preview identity mismatch");
    const data = imageData(result);
    if (!data) throw new Error("preview unavailable");
    selectedMedia = item;
    selectedFrameIndex = boundedIndex;
    previewImage.src = data;
    previewKind.textContent = item.kind === "snapshot" ? "STORED SNAPSHOT" : "STORED BOUNDED CLIP";
    previewId.textContent = shortMediaId(item.id);
    previewId.title = item.id;
    previewMeta.textContent = `${formatMediaTime(item.created_at)} · ${item.width} × ${item.height} · ${formatBytes(item.size_bytes)}`;
    previewControls.hidden = item.kind !== "clip";
    previewPrevious.disabled = boundedIndex === 0;
    previewNext.disabled = boundedIndex >= item.frame_count - 1;
    previewFrame.textContent = `${boundedIndex + 1} / ${item.frame_count}`;
    mediaPreview.hidden = false;
    setMessage("Stored media preview loaded through a bounded opaque-ID operation");
  } catch {
    if (!contextIsCurrent(context) || requestSerial !== previewRequestSerial) return;
    setMessage("Stored media preview could not be loaded", true);
  }
}

function closePreview(): void {
  previewRequestSerial += 1;
  selectedMedia = null;
  selectedFrameIndex = 0;
  previewImage.removeAttribute("src");
  mediaPreview.hidden = true;
}

function downloadFreshTarget(data: string): void {
  const link = document.createElement("a");
  const timestamp = new Date().toISOString().replaceAll(/[:.]/g, "-");
  link.href = data;
  link.download = `noob-target-${timestamp}.jpg`;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
}

app.ontoolresult = (result) => {
  const state = structured(result) as OpenState;
  if (
    state.ui_version !== "2"
    || typeof state.device_id !== "string"
    || !/^noob_[a-z0-9]{16,64}$/.test(state.device_id)
  ) return;
  resetDeviceBoundState(state.device_id);
  const initialSource = state.initial_source_id === "environment" ? "environment" : "target";
  const initialView = state.tab === "storage" || state.tab === "health" ? state.tab : "live";
  selectSource(initialSource, false);
  selectView(initialView, true);
  void refreshStatus();
};
app.onhostcontextchanged = hostContext;

document.querySelectorAll<HTMLButtonElement>(".view-tab").forEach((button) => {
  button.addEventListener("click", () => selectView(button.dataset.view as ViewTab));
});
document.querySelectorAll<HTMLButtonElement>(".source-tab").forEach((button) => {
  button.addEventListener("click", () => selectSource(button.dataset.source as SourceId));
});
element("refresh").addEventListener("click", () => void refreshFrame(source));
element("zoom-out").addEventListener("click", () => { zoom = Math.max(0, zoom - 1); applyZoom(); });
element("zoom-in").addEventListener("click", () => { zoom = Math.min(3, zoom + 1); applyZoom(); });
zoomLabel.addEventListener("click", () => { zoom = 0; applyZoom(); });
element("fullscreen").addEventListener("click", async () => {
  try {
    if (window.openai?.requestDisplayMode) await window.openai.requestDisplayMode({ mode: "fullscreen" });
    else await document.documentElement.requestFullscreen();
  } catch { setMessage("This host did not grant fullscreen", true); }
});
cameraToggle.addEventListener("click", async () => {
  const context = currentDeviceContext();
  if (!context || source !== "environment") return;
  cameraToggle.disabled = true;
  try {
    const nextEnabled = !cameraEnabled;
    const result = await app.callServerTool({
      name: "noob_set_camera_streaming",
      arguments: {
        device_id: context.deviceId,
        enabled: nextEnabled,
        expected_generation: generation,
        request_id: crypto.randomUUID(),
      },
    });
    if (!contextIsCurrent(context)) return;
    if (result.isError) throw new Error("camera state rejected");
    if (!resultMatchesDevice(result, context)) throw new Error("camera device mismatch");
    setMessage(nextEnabled
      ? "Camera stream requested on"
      : "Camera sensor/stream requested off; 5V remains present");
    await refreshStatus(context);
    await refreshFrame("environment", context);
  } catch {
    if (contextIsCurrent(context)) setMessage("Camera state change was not confirmed", true);
  } finally {
    if (contextIsCurrent(context)) cameraToggle.disabled = source !== "environment";
  }
});
screenshot.addEventListener("click", async () => {
  const context = currentDeviceContext();
  if (!context) return;
  const requestedSource = source;
  screenshot.disabled = true;
  try {
    const fresh = await refreshFrame(requestedSource, context);
    if (!contextIsCurrent(context)) return;
    if (!fresh?.state.frame_token) throw new Error("fresh frame unavailable");
    if (requestedSource === "target") {
      downloadFreshTarget(fresh.data);
      setMessage("Fresh target frame downloaded locally; camera microSD was not used");
    } else {
      const result = await app.callServerTool({
        name: "noob_save_screenshot",
        arguments: {
          device_id: context.deviceId,
          source_id: "environment",
          expected_frame_token: fresh.state.frame_token,
          request_id: crypto.randomUUID(),
        },
      });
      if (!contextIsCurrent(context)) return;
      if (result.isError) throw new Error("screenshot rejected");
      if (!resultMatchesDevice(result, context)) throw new Error("screenshot device mismatch");
      setMessage("Explicit environmental snapshot stored on camera microSD");
    }
  } catch {
    if (!contextIsCurrent(context)) return;
    setMessage(
      requestedSource === "target"
        ? "Fresh target frame was not downloaded"
        : "Environmental snapshot was not stored",
      true,
    );
  } finally {
    if (contextIsCurrent(context)) updateScreenshotAction();
  }
});
element("storage-refresh").addEventListener("click", () => void refreshMedia(true));
mediaMore.addEventListener("click", () => void refreshMedia(false));
element("preview-close").addEventListener("click", closePreview);
previewPrevious.addEventListener("click", () => {
  if (selectedMedia?.kind === "clip") void openMedia(selectedMedia, selectedFrameIndex - 1);
});
previewNext.addEventListener("click", () => {
  if (selectedMedia?.kind === "clip") void openMedia(selectedMedia, selectedFrameIndex + 1);
});
element("health-refresh").addEventListener("click", () => void refreshStatus());
element("release").addEventListener("click", async () => {
  const context = currentDeviceContext();
  if (!context) return;
  try {
    const result = await app.callServerTool({
      name: "noob_emergency_release_all",
      arguments: { device_id: context.deviceId, reason: "widget emergency release", request_id: crypto.randomUUID() },
    });
    if (!contextIsCurrent(context)) return;
    if (result.isError) throw new Error("release unavailable");
    setMessage("Emergency input release completed");
    await refreshStatus(context);
  } catch {
    if (contextIsCurrent(context)) setMessage("Emergency release could not be confirmed", true);
  }
});

app.connect().then(() => {
  const context = app.getHostContext();
  if (context) hostContext(context);
  window.setInterval(() => {
    if (document.visibilityState !== "visible" || !deviceId) return;
    void refreshStatus();
    if (activeView === "live") void refreshFrame(source);
  }, 1_000);
});
