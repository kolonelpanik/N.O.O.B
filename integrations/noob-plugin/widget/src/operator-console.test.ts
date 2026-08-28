// @vitest-environment happy-dom

import { readFile } from "node:fs/promises";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface ToolRequest {
  name: string;
  arguments: Record<string, unknown>;
}

interface FakeAppInstance {
  ontoolresult?: (result: Record<string, unknown>) => void;
}

const harness = vi.hoisted(() => ({
  instance: null as FakeAppInstance | null,
  calls: [] as ToolRequest[],
  responder: null as ((request: ToolRequest) => Promise<Record<string, unknown>>) | null,
}));

vi.mock("@modelcontextprotocol/ext-apps", () => {
  class FakeApp {
    ontoolresult?: (result: Record<string, unknown>) => void;
    onhostcontextchanged?: (context: unknown) => void;

    constructor() {
      harness.instance = this;
    }

    async callServerTool(request: ToolRequest): Promise<Record<string, unknown>> {
      harness.calls.push(request);
      if (!harness.responder) throw new Error("missing_test_responder");
      return await harness.responder(request);
    }

    async connect(): Promise<void> {}

    getHostContext(): null {
      return null;
    }
  }

  return {
    App: FakeApp,
    applyDocumentTheme: vi.fn(),
    applyHostFonts: vi.fn(),
    applyHostStyleVariables: vi.fn(),
  };
});

const DEVICE_ID = `noob_${"a".repeat(16)}`;
const DEVICE_B_ID = `noob_${"b".repeat(16)}`;
const SNAPSHOT_ID = `m_${"1".repeat(32)}`;
const CLIP_ID = `m_${"2".repeat(32)}`;
const SNAPSHOT_B_ID = `m_${"3".repeat(32)}`;
const JPEG_BASE64 = "/9j/2Q==";
const JPEG_B_BASE64 = "/9j/4AAQ==";
const widgetHtml = await readFile(path.resolve(process.cwd(), "widget/operator-console.html"), "utf8");
const widgetBody = widgetHtml.match(/<body>([\s\S]*)<\/body>/)?.[1] ?? "";

function toolResult(structuredContent: Record<string, unknown>, image: false | string = false): Record<string, unknown> {
  return {
    structuredContent,
    content: image
      ? [{ type: "image", mimeType: "image/jpeg", data: image }]
      : [{ type: "text", text: "ok" }],
  };
}

function frameResult(
  source: "target" | "environment",
  sequence: number,
  deviceId = DEVICE_ID,
  image = JPEG_BASE64,
): Record<string, unknown> {
  return toolResult({
    device_id: deviceId,
    source_id: source,
    frame_token: `ft1.${"a".repeat(20)}.${"b".repeat(40)}`,
    sequence,
    generation: 7,
  }, image);
}

function statusResult(deviceId = DEVICE_ID, options: {
  ok?: boolean;
  videoReady?: boolean;
  serialReady?: boolean;
  controlActive?: boolean;
  cameraEnabled?: boolean;
} = {}): Record<string, unknown> {
  return toolResult({
    device_id: deviceId,
    status: {
      ok: options.ok ?? true,
      video: { ready: options.videoReady ?? true },
      serial: { ready: options.serialReady ?? true },
      control: { active: options.controlActive ?? false },
      environment_camera: {
        configured: true,
        reachable: true,
        frame_ready: true,
        stream_enabled: options.cameraEnabled ?? true,
        generation: 7,
        storage: { mounted: true, writable: true },
      },
    },
  });
}

function mediaItem(id: string): Record<string, unknown> {
  return {
    id,
    kind: "snapshot",
    state: "complete",
    created_at: "2026-08-28T00:00:00.000Z",
    size_bytes: 4_096,
    width: 640,
    height: 480,
    frame_count: 1,
    duration_ms: 0,
  };
}

function mediaPage(deviceId: string, items: Record<string, unknown>[]): Record<string, unknown> {
  return toolResult({
    device_id: deviceId,
    media: { ok: true, items, next_cursor: null },
  });
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolver) => { resolve = resolver; });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

async function mount(options: {
  deviceId?: string;
  source?: "target" | "environment";
  tab?: "live" | "storage" | "health";
  responder: (request: ToolRequest) => Promise<Record<string, unknown>>;
}): Promise<void> {
  harness.responder = options.responder;
  await import("./operator-console.js");
  openDevice(options.deviceId ?? DEVICE_ID, options.source, options.tab);
  await settle();
}

function openDevice(
  deviceId: string,
  source: "target" | "environment" = "target",
  tab: "live" | "storage" | "health" = "live",
): void {
  harness.instance?.ontoolresult?.(toolResult({
    device_id: deviceId,
    initial_source_id: source,
    tab,
    ui_version: "2",
  }));
}

beforeEach(() => {
  vi.resetModules();
  harness.instance = null;
  harness.calls = [];
  harness.responder = null;
  document.body.innerHTML = widgetBody;
  vi.spyOn(window, "setInterval").mockReturnValue(1 as unknown as ReturnType<typeof window.setInterval>);
});

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("N.O.O.B. operator widget", () => {
  it("downloads Target screenshots only from a newly bounded frame and never calls camera storage", async () => {
    let sequence = 10;
    let downloadedHref = "";
    let downloadedName = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloadedHref = this.href;
      downloadedName = this.download;
    });
    await mount({
      responder: async (request) => {
        if (request.name === "noob_get_status") return statusResult();
        if (request.name === "noob_widget_poll_frame") {
          sequence += 1;
          return frameResult("target", sequence);
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    const initialPolls = harness.calls.filter((call) => call.name === "noob_widget_poll_frame").length;
    (document.getElementById("screenshot") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(downloadedHref).toBe(`data:image/jpeg;base64,${JPEG_BASE64}`));

    const polls = harness.calls.filter((call) => call.name === "noob_widget_poll_frame");
    expect(polls).toHaveLength(initialPolls + 1);
    expect(polls.at(-1)?.arguments).toEqual({ device_id: DEVICE_ID, source_id: "target" });
    expect(harness.calls.some((call) => call.name === "noob_save_screenshot")).toBe(false);
    expect(downloadedName).toMatch(/^noob-target-.*\.jpg$/);
    expect(document.getElementById("message")?.textContent).toContain("camera microSD was not used");
  });

  it("stores Environment screenshots with the literal environment source and fresh frame token", async () => {
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    await mount({
      source: "environment",
      responder: async (request) => {
        if (request.name === "noob_get_status") return statusResult();
        if (request.name === "noob_widget_poll_frame") return frameResult("environment", 30);
        if (request.name === "noob_save_screenshot") {
          return toolResult({ device_id: DEVICE_ID, ok: true, item: { id: SNAPSHOT_ID } });
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    (document.getElementById("screenshot") as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(harness.calls.some((call) => call.name === "noob_save_screenshot")).toBe(true);
    });

    const save = harness.calls.find((call) => call.name === "noob_save_screenshot");
    expect(save?.arguments).toMatchObject({
      device_id: DEVICE_ID,
      source_id: "environment",
      expected_frame_token: `ft1.${"a".repeat(20)}.${"b".repeat(40)}`,
    });
    expect(Object.keys(save?.arguments ?? {}).sort()).toEqual([
      "device_id",
      "expected_frame_token",
      "request_id",
      "source_id",
    ]);
    expect(anchorClick).not.toHaveBeenCalled();
    expect(document.getElementById("message")?.textContent).toContain("camera microSD");
  });

  it("honors the storage tab and previews only opaque IDs through bounded existing tools", async () => {
    await mount({
      tab: "storage",
      responder: async (request) => {
        if (request.name === "noob_get_status") return statusResult();
        if (request.name === "noob_list_media") {
          return toolResult({
            device_id: DEVICE_ID,
            media: {
              ok: true,
              items: [
                {
                  id: SNAPSHOT_ID,
                  kind: "snapshot",
                  state: "complete",
                  created_at: "2026-08-28T00:00:00.000Z",
                  size_bytes: 4_096,
                  width: 640,
                  height: 480,
                  frame_count: 1,
                  duration_ms: 0,
                },
                {
                  id: CLIP_ID,
                  kind: "clip",
                  state: "complete",
                  created_at: null,
                  size_bytes: 12_288,
                  width: 640,
                  height: 480,
                  frame_count: 3,
                  duration_ms: 1_000,
                },
                {
                  id: "../../camera/private.jpg",
                  kind: "snapshot",
                  state: "complete",
                  frame_count: 1,
                },
                {
                  id: "https://example.invalid/camera.jpg",
                  kind: "snapshot",
                  state: "complete",
                  frame_count: 1,
                },
              ],
              next_cursor: null,
            },
          });
        }
        if (request.name === "noob_get_media") {
          return toolResult({ device_id: DEVICE_ID, media_id: SNAPSHOT_ID }, JPEG_BASE64);
        }
        if (request.name === "noob_get_clip_frame") {
          return toolResult(
            { device_id: DEVICE_ID, media_id: CLIP_ID, frame_index: request.arguments.frame_index },
            JPEG_BASE64,
          );
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    expect((document.getElementById("storage-view") as HTMLElement).hidden).toBe(false);
    expect((document.getElementById("live-view") as HTMLElement).hidden).toBe(true);
    expect(document.querySelectorAll<HTMLButtonElement>(".media-item")).toHaveLength(2);
    expect(harness.calls.some((call) => call.name === "noob_widget_poll_frame")).toBe(false);

    document.querySelector<HTMLButtonElement>(`[data-media-id="${SNAPSHOT_ID}"]`)?.click();
    await vi.waitFor(() => expect((document.getElementById("media-preview") as HTMLElement).hidden).toBe(false));
    expect(harness.calls.find((call) => call.name === "noob_get_media")?.arguments).toEqual({
      device_id: DEVICE_ID,
      media_id: SNAPSHOT_ID,
    });

    document.querySelector<HTMLButtonElement>(`[data-media-id="${CLIP_ID}"]`)?.click();
    await vi.waitFor(() => expect(harness.calls.some((call) => call.name === "noob_get_clip_frame")).toBe(true));
    expect(harness.calls.find((call) => call.name === "noob_get_clip_frame")?.arguments).toEqual({
      device_id: DEVICE_ID,
      media_id: CLIP_ID,
      frame_index: 0,
    });
    expect(harness.calls.some((call) => /delete/i.test(call.name))).toBe(false);
    expect(harness.calls.every((call) => !("path" in call.arguments) && !("url" in call.arguments))).toBe(true);
    expect(document.querySelector("[data-path], [data-url], input[type=url]")).toBeNull();
  });

  it("honors the health tab without starting frame or storage activity", async () => {
    await mount({
      tab: "health",
      responder: async (request) => {
        if (request.name === "noob_get_status") return statusResult();
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    expect((document.getElementById("health-view") as HTMLElement).hidden).toBe(false);
    expect((document.getElementById("live-view") as HTMLElement).hidden).toBe(true);
    expect((document.getElementById("storage-view") as HTMLElement).hidden).toBe(true);
    expect(document.getElementById("health-video")?.textContent).toBe("READY");
    expect(document.getElementById("health-serial")?.textContent).toBe("READY");
    expect(document.getElementById("health-control")?.textContent).toBe("OBSERVE ONLY");
    expect(harness.calls.some((call) => call.name === "noob_widget_poll_frame")).toBe(false);
    expect(harness.calls.some((call) => call.name === "noob_list_media")).toBe(false);
    expect(harness.calls.every((call) => call.name === "noob_get_status")).toBe(true);
  });

  it("discards a deferred Device A frame after switching to Device B", async () => {
    const lateFrame = deferred<Record<string, unknown>>();
    await mount({
      responder: async (request) => {
        const requestedDevice = request.arguments.device_id;
        if (request.name === "noob_get_status") return statusResult(String(requestedDevice));
        if (request.name === "noob_widget_poll_frame" && requestedDevice === DEVICE_ID) {
          return await lateFrame.promise;
        }
        if (request.name === "noob_widget_poll_frame" && requestedDevice === DEVICE_B_ID) {
          return frameResult("target", 202, DEVICE_B_ID, JPEG_B_BASE64);
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    openDevice(DEVICE_B_ID);
    await vi.waitFor(() => {
      expect(document.getElementById("device")?.textContent).toBe(DEVICE_B_ID);
      expect(document.getElementById("frame-state")?.textContent).toBe("LIVE · 202");
    });

    lateFrame.resolve(frameResult("target", 101, DEVICE_ID, JPEG_BASE64));
    await settle();

    expect((document.getElementById("frame") as HTMLImageElement).src).toBe(
      `data:image/jpeg;base64,${JPEG_B_BASE64}`,
    );
    expect(document.getElementById("frame-state")?.textContent).toBe("LIVE · 202");
    const frameCalls = harness.calls.filter((call) => call.name === "noob_widget_poll_frame");
    expect(frameCalls.map((call) => call.arguments.device_id)).toEqual([DEVICE_ID, DEVICE_B_ID]);
  });

  it("discards a deferred Device A status after switching to Device B", async () => {
    const lateStatus = deferred<Record<string, unknown>>();
    await mount({
      responder: async (request) => {
        const requestedDevice = request.arguments.device_id;
        if (request.name === "noob_get_status" && requestedDevice === DEVICE_ID) {
          return await lateStatus.promise;
        }
        if (request.name === "noob_get_status" && requestedDevice === DEVICE_B_ID) {
          return statusResult(DEVICE_B_ID, {
            videoReady: true,
            serialReady: true,
            controlActive: false,
            cameraEnabled: false,
          });
        }
        if (request.name === "noob_widget_poll_frame") {
          return frameResult("target", requestedDevice === DEVICE_ID ? 101 : 202, String(requestedDevice));
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    openDevice(DEVICE_B_ID);
    await vi.waitFor(() => {
      expect(document.getElementById("health-video")?.textContent).toBe("READY");
      expect(document.getElementById("health-control")?.textContent).toBe("OBSERVE ONLY");
      expect(document.getElementById("camera-state")?.textContent).toBe("STREAM OFF");
    });

    lateStatus.resolve(statusResult(DEVICE_ID, {
      videoReady: false,
      serialReady: false,
      controlActive: true,
      cameraEnabled: true,
    }));
    await settle();

    expect(document.getElementById("device")?.textContent).toBe(DEVICE_B_ID);
    expect(document.getElementById("health-video")?.textContent).toBe("READY");
    expect(document.getElementById("health-serial")?.textContent).toBe("READY");
    expect(document.getElementById("health-control")?.textContent).toBe("OBSERVE ONLY");
    expect(document.getElementById("proof")?.textContent).toBe("OBSERVE ONLY");
    expect(document.getElementById("camera-state")?.textContent).toBe("STREAM OFF");
  });

  it("discards a deferred Device A storage list after switching to Device B", async () => {
    const lateList = deferred<Record<string, unknown>>();
    await mount({
      tab: "storage",
      responder: async (request) => {
        const requestedDevice = request.arguments.device_id;
        if (request.name === "noob_get_status") return statusResult(String(requestedDevice));
        if (request.name === "noob_list_media" && requestedDevice === DEVICE_ID) {
          return await lateList.promise;
        }
        if (request.name === "noob_list_media" && requestedDevice === DEVICE_B_ID) {
          return mediaPage(DEVICE_B_ID, [mediaItem(SNAPSHOT_B_ID)]);
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    openDevice(DEVICE_B_ID, "target", "storage");
    await vi.waitFor(() => {
      expect(document.querySelector(`[data-media-id="${SNAPSHOT_B_ID}"]`)).not.toBeNull();
    });

    lateList.resolve(mediaPage(DEVICE_ID, [mediaItem(SNAPSHOT_ID)]));
    await settle();

    expect(document.getElementById("device")?.textContent).toBe(DEVICE_B_ID);
    expect(document.querySelector(`[data-media-id="${SNAPSHOT_B_ID}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-media-id="${SNAPSHOT_ID}"]`)).toBeNull();
    expect(document.querySelectorAll(".media-item")).toHaveLength(1);
  });

  it("discards a deferred Device A media preview after switching to and previewing Device B", async () => {
    const latePreview = deferred<Record<string, unknown>>();
    await mount({
      tab: "storage",
      responder: async (request) => {
        const requestedDevice = request.arguments.device_id;
        if (request.name === "noob_get_status") return statusResult(String(requestedDevice));
        if (request.name === "noob_list_media") {
          return requestedDevice === DEVICE_ID
            ? mediaPage(DEVICE_ID, [mediaItem(SNAPSHOT_ID)])
            : mediaPage(DEVICE_B_ID, [mediaItem(SNAPSHOT_B_ID)]);
        }
        if (request.name === "noob_get_media" && requestedDevice === DEVICE_ID) {
          return await latePreview.promise;
        }
        if (request.name === "noob_get_media" && requestedDevice === DEVICE_B_ID) {
          return toolResult(
            { device_id: DEVICE_B_ID, media_id: SNAPSHOT_B_ID },
            JPEG_B_BASE64,
          );
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    document.querySelector<HTMLButtonElement>(`[data-media-id="${SNAPSHOT_ID}"]`)?.click();
    await vi.waitFor(() => {
      expect(harness.calls.some((call) => call.name === "noob_get_media" && call.arguments.device_id === DEVICE_ID)).toBe(true);
    });

    openDevice(DEVICE_B_ID, "target", "storage");
    await vi.waitFor(() => {
      expect(document.querySelector(`[data-media-id="${SNAPSHOT_B_ID}"]`)).not.toBeNull();
    });
    document.querySelector<HTMLButtonElement>(`[data-media-id="${SNAPSHOT_B_ID}"]`)?.click();
    await vi.waitFor(() => {
      expect((document.getElementById("media-preview") as HTMLElement).hidden).toBe(false);
      expect(document.getElementById("preview-id")?.getAttribute("title")).toBe(SNAPSHOT_B_ID);
    });

    latePreview.resolve(toolResult(
      { device_id: DEVICE_ID, media_id: SNAPSHOT_ID },
      JPEG_BASE64,
    ));
    await settle();

    expect(document.getElementById("device")?.textContent).toBe(DEVICE_B_ID);
    expect(document.getElementById("preview-id")?.getAttribute("title")).toBe(SNAPSHOT_B_ID);
    expect((document.getElementById("preview-image") as HTMLImageElement).src).toBe(
      `data:image/jpeg;base64,${JPEG_B_BASE64}`,
    );
  });

  it("fails closed when a current request returns a different device identity", async () => {
    await mount({
      responder: async (request) => {
        if (request.name === "noob_get_status") return statusResult(DEVICE_B_ID);
        if (request.name === "noob_widget_poll_frame") {
          return frameResult("target", 999, DEVICE_B_ID, JPEG_B_BASE64);
        }
        throw new Error(`unexpected_tool_${request.name}`);
      },
    });

    expect(document.getElementById("device")?.textContent).toBe(DEVICE_ID);
    expect(document.getElementById("gateway-state")?.textContent).toBe("DEGRADED");
    expect(document.getElementById("frame-state")?.textContent).toBe("UNAVAILABLE");
    expect((document.getElementById("frame") as HTMLImageElement).hidden).toBe(true);
  });
});
