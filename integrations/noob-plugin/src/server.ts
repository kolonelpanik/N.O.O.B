import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { registerAppResource, registerAppTool, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult, ReadResourceResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { publicError } from "./policy.js";
import { NoobRuntime } from "./runtime.js";
import {
  CameraJobId,
  CandidateId,
  ClipFrameIndex,
  ControlSessionId,
  DeviceId,
  FrameToken,
  InputAnnotations,
  KeyName,
  LocalReadAnnotations,
  MediaId,
  MouseButton,
  ReadAnnotations,
  RequestId,
  SourceId,
  WriteAnnotations,
} from "./schemas.js";

const WIDGET_URI = "ui://noob/operator-console/v1/index.html";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const widgetPath = path.join(__dirname, "widget", "widget", "operator-console.html");

function textResult(structuredContent: object, message = "N.O.O.B. operation completed."): CallToolResult {
  return { content: [{ type: "text", text: message }], structuredContent: structuredContent as Record<string, unknown> };
}

function imageResult(structuredContent: object, bytes: Uint8Array): CallToolResult {
  return {
    content: [
      { type: "text", text: "Gateway-accepted N.O.O.B. frame observed. The hardware capture time is not exposed; target acceptance remains unverified until a newer frame shows the requested effect." },
      { type: "image", mimeType: "image/jpeg", data: Buffer.from(bytes).toString("base64") },
    ],
    structuredContent: structuredContent as Record<string, unknown>,
  };
}

function storedImageResult(structuredContent: object, bytes: Uint8Array): CallToolResult {
  return {
    content: [
      { type: "text", text: "Completed environmental-camera media retrieved from camera-owned storage." },
      { type: "image", mimeType: "image/jpeg", data: Buffer.from(bytes).toString("base64") },
    ],
    structuredContent: structuredContent as Record<string, unknown>,
  };
}

function failed(error: unknown): CallToolResult {
  const code = publicError(error);
  return { isError: true, content: [{ type: "text", text: `N.O.O.B. operation failed: ${code}.` }], structuredContent: { ok: false, error: code } };
}

function handler<T>(operation: (input: T) => Promise<CallToolResult>): (input: T) => Promise<CallToolResult> {
  return async (input) => {
    try { return await operation(input); } catch (error) { return failed(error); }
  };
}

export function createServer(runtime: NoobRuntime): McpServer {
  const server = new McpServer(
    { name: "noob-mcp-server", version: "0.2.0" },
    {
      instructions: "Observe by default. Before target input, obtain a fresh target frame and acquire bounded control. Never infer target acceptance from a UART acknowledgement; verify using a newer frame. Never expose credentials, leases, typed text, or frame bytes in logs.",
    },
  );

  server.registerTool("noob_list_devices", {
    title: "List paired N.O.O.B. devices",
    description: "Lists locally registered N.O.O.B. profiles and their connection states. This performs no network scan.",
    inputSchema: z.object({}).strict(),
    annotations: LocalReadAnnotations,
  }, handler(async () => textResult(await runtime.listDevices(), "Paired N.O.O.B. device profiles.")));

  server.registerTool("noob_discover_devices", {
    title: "Discover N.O.O.B. devices",
    description: "Performs a bounded local mDNS lookup for _noob-kvm._tcp. Results are untrusted hints until the SSH host-key fingerprint is independently verified.",
    inputSchema: z.object({ timeout_ms: z.number().int().min(250).max(5_000).default(1_500) }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ timeout_ms }) => textResult(await runtime.discover(timeout_ms), "Untrusted local discovery candidates.")));

  server.registerTool("noob_probe_device", {
    title: "Probe a N.O.O.B. address",
    description: "Probes one private or link-local host address for an SSH host key. It does not authenticate, trust, or connect to the device.",
    inputSchema: z.object({
      address: z.string().min(1).max(253),
      ssh_port: z.number().int().min(1).max(65_535).default(22),
      timeout_ms: z.number().int().min(250).max(5_000).default(1_500),
    }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ address, ssh_port, timeout_ms }) => textResult(await runtime.probe(address, ssh_port, timeout_ms), "Untrusted device fingerprint observed; verify it out of band before registration.")));

  server.registerTool("noob_register_device", {
    title: "Register a verified N.O.O.B. device",
    description: "Pins a previously probed SSH host key after the operator independently confirms its SHA-256 fingerprint. This does not store a password or bearer token.",
    inputSchema: z.object({
      candidate_id: CandidateId,
      expected_host_key_sha256: z.string().regex(/^SHA256:[A-Za-z0-9+/]{43}=?$/),
      profile_name: z.string().trim().min(1).max(64).default("N.O.O.B. appliance"),
      set_default: z.boolean().default(false),
      request_id: RequestId,
    }).strict(),
    annotations: { ...WriteAnnotations, openWorldHint: false },
  }, handler(async ({ candidate_id, expected_host_key_sha256, profile_name, set_default }) => textResult(
    await runtime.register(candidate_id, expected_host_key_sha256, profile_name, set_default),
    "N.O.O.B. device identity pinned locally.",
  )));

  server.registerTool("noob_connect_device", {
    title: "Connect to a paired N.O.O.B. device",
    description: "Opens a loopback-only SSH forward using the pinned host key and dedicated identity. It never exposes the appliance gateway directly on the LAN.",
    inputSchema: z.object({ device_id: DeviceId, request_id: RequestId }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ device_id }) => textResult(await runtime.connect(device_id), "Pinned SSH tunnel connected.")));

  server.registerTool("noob_get_status", {
    title: "Get N.O.O.B. proof status",
    description: "Reads the current gateway, target video, UART, control, environmental-camera, and storage proof states. Transport readiness is kept distinct from target-visible acceptance.",
    inputSchema: z.object({ device_id: DeviceId }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id }) => textResult({ device_id, status: await runtime.status(device_id) }, "Current N.O.O.B. proof status.")));

  server.registerTool("noob_get_frame", {
    title: "Observe a ready N.O.O.B. frame",
    description: "Returns one bounded JPEG that the gateway accepted as fresh at response time, plus an exact-generation, short-lived frame token. It reports observation time and never invents a hardware capture timestamp.",
    inputSchema: z.object({ device_id: DeviceId, source_id: SourceId.default("target") }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id, source_id }) => {
    const result = await runtime.frame(device_id, source_id);
    return imageResult(result.structured, result.bytes);
  }));

  server.registerTool("noob_list_media", {
    title: "List N.O.O.B. camera media",
    description: "Lists a bounded page of completed environmental-camera media using opaque IDs. It never accepts camera filesystem paths.",
    inputSchema: z.object({
      device_id: DeviceId,
      cursor: z.string().max(128).optional(),
      limit: z.number().int().min(1).max(50).default(20),
    }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id, cursor, limit }) => textResult({ device_id, media: await runtime.listMedia(device_id, cursor, limit) }, "Camera media page.")));

  server.registerTool("noob_get_media", {
    title: "Get N.O.O.B. camera media",
    description: "Retrieves metadata or a bounded JPEG for one opaque environmental-camera media ID.",
    inputSchema: z.object({ device_id: DeviceId, media_id: MediaId }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id, media_id }) => {
    const result = await runtime.getMedia(device_id, media_id);
    return result.bytes ? storedImageResult({ device_id, media_id, metadata: result.metadata }, result.bytes) : textResult({ device_id, media_id, metadata: result.metadata });
  }));

  server.registerTool("noob_get_clip_frame", {
    title: "Get one N.O.O.B. camera clip frame",
    description: "Retrieves one JPEG frame from a completed environmental-camera clip using an opaque media ID and an index bounded to 0 through 149. It never accepts a filesystem path.",
    inputSchema: z.object({
      device_id: DeviceId,
      media_id: MediaId,
      frame_index: ClipFrameIndex,
    }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id, media_id, frame_index }) => {
    const result = await runtime.getClipFrame(device_id, media_id, frame_index);
    return storedImageResult(
      {
        device_id,
        media_id,
        frame_index: result.frameIndex,
        metadata: result.metadata,
      },
      result.bytes,
    );
  }));

  registerAppTool(server, "noob_open_console", {
    title: "Open the N.O.O.B. operator console",
    description: "Renders the interactive observe-first N.O.O.B. console. It does not acquire target control or start recording when opened.",
    inputSchema: z.object({
      device_id: DeviceId,
      initial_source_id: SourceId.default("target"),
      tab: z.enum(["live", "storage", "health"]).default("live"),
    }).strict(),
    annotations: ReadAnnotations,
    _meta: { ui: { resourceUri: WIDGET_URI }, "openai/outputTemplate": WIDGET_URI },
  }, handler(async ({ device_id, initial_source_id, tab }) => textResult({ device_id, initial_source_id, tab, ui_version: "1" }, "N.O.O.B. operator console ready.")));

  server.registerTool("noob_set_camera_streaming", {
    title: "Set environmental camera state",
    description: "Explicitly enables or disables the environmental camera sensor/stream using generation conflict protection. This does not prove electrical 5V power removal.",
    inputSchema: z.object({
      device_id: DeviceId,
      enabled: z.boolean(),
      expected_generation: z.number().int().nonnegative(),
      request_id: RequestId,
    }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ device_id, enabled, expected_generation }) => textResult(
    { device_id, camera: await runtime.setCameraStreaming(device_id, enabled, expected_generation) },
    enabled ? "Environmental camera stream requested on." : "Environmental camera sensor/stream requested off; device power remains present.",
  )));

  server.registerTool("noob_save_screenshot", {
    title: "Save a N.O.O.B. screenshot",
    description: "Explicitly stores a screenshot bound to a fresh frame token. No automatic recording occurs.",
    inputSchema: z.object({
      device_id: DeviceId,
      source_id: z.literal("environment"),
      expected_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: { ...WriteAnnotations, idempotentHint: false },
  }, handler(async ({ device_id, expected_frame_token }) => textResult(
    { device_id, item: await runtime.saveScreenshot(device_id, expected_frame_token) },
    "Explicit screenshot stored.",
  )));

  server.registerTool("noob_start_recording", {
    title: "Start a bounded camera recording",
    description: "Starts one explicit bounded environmental-camera JPEG clip. The operation automatically stops at the declared maximum duration.",
    inputSchema: z.object({
      device_id: DeviceId,
      duration_seconds: z.number().int().min(1).max(30),
      fps: z.number().int().min(1).max(5).default(3),
      expected_generation: z.number().int().nonnegative(),
      request_id: RequestId,
    }).strict(),
    annotations: { ...WriteAnnotations, idempotentHint: false },
  }, handler(async ({ device_id, duration_seconds, fps, expected_generation }) => textResult(
    { device_id, recording: await runtime.startRecording(device_id, duration_seconds, fps, expected_generation) },
    "Bounded environmental-camera recording started.",
  )));

  server.registerTool("noob_stop_recording", {
    title: "Stop a bounded camera recording",
    description: "Cancels one active environmental-camera recording. Its unpublished partial clip is removed and no completed media item is created.",
    inputSchema: z.object({
      device_id: DeviceId,
      recording_job_id: CameraJobId,
      request_id: RequestId,
    }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ device_id, recording_job_id }) => textResult(
    { device_id, recording: await runtime.stopRecording(device_id, recording_job_id) },
    "Camera recording cancellation requested; poll until the job reports cancelled.",
  )));

  server.registerTool("noob_get_recording_status", {
    title: "Get bounded camera recording status",
    description: "Reads one environmental-camera clip job using its fixed opaque job ID. Poll until complete, failed, or cancelled.",
    inputSchema: z.object({
      device_id: DeviceId,
      recording_job_id: CameraJobId,
    }).strict(),
    annotations: ReadAnnotations,
  }, handler(async ({ device_id, recording_job_id }) => textResult(
    { device_id, recording: await runtime.recordingStatus(device_id, recording_job_id) },
    "Current bounded camera recording status.",
  )));

  server.registerTool("noob_acquire_control", {
    title: "Acquire bounded N.O.O.B. target control",
    description: "Acquires an exclusive, bounded target-input session only after a fresh target frame was observed. Opening the console never calls this automatically.",
    inputSchema: z.object({
      device_id: DeviceId,
      purpose: z.string().trim().min(1).max(120),
      observed_frame_token: FrameToken,
      max_duration_seconds: z.number().int().min(5).max(120).default(30),
      request_id: RequestId,
    }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ device_id, observed_frame_token, max_duration_seconds }) => textResult(
    await runtime.acquireControl(device_id, observed_frame_token, max_duration_seconds),
    "Bounded target control acquired. A fresh target frame is still required for every input.",
  )));

  server.registerTool("noob_type_text", {
    title: "Type text through N.O.O.B.",
    description: "Types bounded printable text through the Pico HID path using an active control session and fresh target frame. Do not use this tool for passwords or other private values.",
    inputSchema: z.object({
      control_session_id: ControlSessionId,
      text: z.string().min(1).max(512).regex(/^[\x20-\x7e\t\r\n]+$/),
      interval_ms: z.number().int().min(0).max(25).default(0),
      observed_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: InputAnnotations,
  }, handler(async ({ control_session_id, text, interval_ms, observed_frame_token, request_id }) => textResult(
    await runtime.typeText(control_session_id, text, interval_ms, observed_frame_token, request_id),
    "Text transport acknowledged; target-visible acceptance remains unverified until a newer frame is inspected.",
  )));

  server.registerTool("noob_press_key_combo", {
    title: "Press a N.O.O.B. key combination",
    description: "Sends one bounded key combination through the Pico HID path. Requires active control and a fresh target frame.",
    inputSchema: z.object({
      control_session_id: ControlSessionId,
      keys: z.array(KeyName).min(1).max(6),
      hold_ms: z.number().int().min(20).max(500).default(50),
      observed_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: InputAnnotations,
  }, handler(async ({ control_session_id, keys, hold_ms, observed_frame_token, request_id }) => textResult(
    await runtime.combo(control_session_id, keys, hold_ms, observed_frame_token, request_id),
    "Key-combination transport acknowledged; inspect a newer frame to verify the target effect.",
  )));

  server.registerTool("noob_move_pointer", {
    title: "Move the N.O.O.B. pointer",
    description: "Sends one bounded relative pointer movement through the Pico HID path. Requires active control and a fresh target frame.",
    inputSchema: z.object({
      control_session_id: ControlSessionId,
      dx: z.number().int().min(-127).max(127),
      dy: z.number().int().min(-127).max(127),
      wheel: z.number().int().min(-20).max(20).default(0),
      observed_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: InputAnnotations,
  }, handler(async ({ control_session_id, dx, dy, wheel, observed_frame_token, request_id }) => textResult(
    await runtime.move(control_session_id, dx, dy, wheel, observed_frame_token, request_id),
    "Pointer transport acknowledged; inspect a newer frame to verify target movement.",
  )));

  server.registerTool("noob_click_pointer", {
    title: "Click the N.O.O.B. pointer",
    description: "Sends one to three bounded pointer clicks through the Pico HID path. Requires active control and a fresh target frame.",
    inputSchema: z.object({
      control_session_id: ControlSessionId,
      button: MouseButton,
      count: z.number().int().min(1).max(3).default(1),
      interval_ms: z.number().int().min(50).max(500).default(100),
      observed_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: InputAnnotations,
  }, handler(async ({ control_session_id, button, count, interval_ms, observed_frame_token, request_id }) => textResult(
    await runtime.click(control_session_id, button, count, interval_ms, observed_frame_token, request_id),
    "Pointer-click transport acknowledged; inspect a newer frame to verify the target effect.",
  )));

  server.registerTool("noob_drag_pointer", {
    title: "Drag the N.O.O.B. pointer",
    description: "Runs a bounded drag as button-down, at most 32 relative steps, and guaranteed button-up cleanup. Requires active control and a fresh target frame.",
    inputSchema: z.object({
      control_session_id: ControlSessionId,
      button: MouseButton,
      path: z.array(z.object({
        dx: z.number().int().min(-127).max(127),
        dy: z.number().int().min(-127).max(127),
        duration_ms: z.number().int().min(10).max(250).default(25),
      }).strict()).min(1).max(32),
      observed_frame_token: FrameToken,
      request_id: RequestId,
    }).strict(),
    annotations: InputAnnotations,
  }, handler(async ({ control_session_id, button, path, observed_frame_token, request_id }) => textResult(
    await runtime.drag(control_session_id, button, path, observed_frame_token, request_id),
    "Bounded drag transport completed with button-release cleanup; inspect a newer frame to verify the target effect.",
  )));

  server.registerTool("noob_release_control", {
    title: "Release N.O.O.B. target control",
    description: "Releases one bounded MCP target-control session and clears its lease.",
    inputSchema: z.object({ control_session_id: ControlSessionId, request_id: RequestId }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ control_session_id }) => textResult(await runtime.releaseControl(control_session_id), "Target control released.")));

  server.registerTool("noob_emergency_release_all", {
    title: "Emergency-release all N.O.O.B. input",
    description: "Safety-restoring operation that releases held keys/buttons and all target-control ownership on one device. It does not require an active control session.",
    inputSchema: z.object({
      device_id: DeviceId,
      reason: z.string().trim().min(1).max(120).optional(),
      request_id: RequestId,
    }).strict(),
    annotations: WriteAnnotations,
  }, handler(async ({ device_id }) => textResult(await runtime.emergencyRelease(device_id), "Emergency target-input release completed.")));

  registerAppTool(server, "noob_widget_poll_frame", {
    title: "Refresh N.O.O.B. console frame",
    description: "App-only bounded frame refresh for the mounted N.O.O.B. widget.",
    inputSchema: z.object({ device_id: DeviceId, source_id: SourceId }).strict(),
    annotations: ReadAnnotations,
    _meta: { ui: { visibility: ["app"] }, "openai/visibility": "private" },
  }, handler(async ({ device_id, source_id }) => {
    const result = await runtime.frame(device_id, source_id);
    return imageResult(result.structured, result.bytes);
  }));

  registerAppResource(server, WIDGET_URI, WIDGET_URI, {
    mimeType: RESOURCE_MIME_TYPE,
    _meta: {
      ui: {
        prefersBorder: true,
        csp: { connectDomains: [], resourceDomains: [] },
      },
      "openai/widgetDescription": "Authenticated N.O.O.B. observe-first live-view and bounded-control console.",
      "openai/widgetPrefersBorder": true,
    },
  }, async (): Promise<ReadResourceResult> => ({
    contents: [{ uri: WIDGET_URI, mimeType: RESOURCE_MIME_TYPE, text: await readFile(widgetPath, "utf8") }],
  }));

  return server;
}
