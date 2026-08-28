import { afterEach, describe, expect, it, vi } from "vitest";
import { GatewayApi } from "./gateway.js";
import { NoobRuntime } from "./runtime.js";

const DEVICE_ID = `noob_${"a".repeat(16)}`;
const MEDIA_ID = `m_${"b".repeat(32)}`;
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);

afterEach(() => vi.restoreAllMocks());

describe("stored clip-frame retrieval", () => {
  it("uses only the fixed opaque-ID route and a bounded completed-clip index", async () => {
    const runtime = new NoobRuntime();
    const metadata = {
      ok: true,
      item: { kind: "clip", state: "complete", frame_count: 3 },
    };
    const api = {
      json: vi.fn(async () => metadata),
      jpeg: vi.fn(async () => JPEG),
    };
    vi.spyOn(runtime.tunnels, "api").mockReturnValue({
      api: api as unknown as GatewayApi,
      view: {
        device_id: DEVICE_ID,
        connection_id: "conn_media",
        connection_state: "connected",
        connected_at: "2026-08-27T21:00:00.000Z",
        local_port: 18765,
      },
    });

    const result = await runtime.getClipFrame(DEVICE_ID, MEDIA_ID, 2);
    expect(result).toMatchObject({ metadata, frameIndex: 2, bytes: JPEG });
    expect(api.json).toHaveBeenCalledWith(
      `/api/v1/environment-camera/storage/${MEDIA_ID}`,
    );
    expect(api.jpeg).toHaveBeenCalledWith(
      `/api/v1/environment-camera/storage/${MEDIA_ID}/frames/2.jpg`,
    );
    await runtime.close();
  });

  it("rejects snapshot media and out-of-manifest indexes before JPEG retrieval", async () => {
    const runtime = new NoobRuntime();
    const api = {
      json: vi.fn()
        .mockResolvedValueOnce({ ok: true, item: { kind: "snapshot", state: "complete", frame_count: 1 } })
        .mockResolvedValueOnce({ ok: true, item: { kind: "clip", state: "complete", frame_count: 2 } }),
      jpeg: vi.fn(),
    };
    vi.spyOn(runtime.tunnels, "api").mockReturnValue({
      api: api as unknown as GatewayApi,
      view: {
        device_id: DEVICE_ID,
        connection_id: "conn_media",
        connection_state: "connected",
        connected_at: "2026-08-27T21:00:00.000Z",
        local_port: 18765,
      },
    });

    await expect(runtime.getClipFrame(DEVICE_ID, MEDIA_ID, 0)).rejects.toThrow("media_not_completed_clip");
    await expect(runtime.getClipFrame(DEVICE_ID, MEDIA_ID, 2)).rejects.toThrow("clip_frame_out_of_range");
    await expect(runtime.getClipFrame(DEVICE_ID, MEDIA_ID, 150)).rejects.toThrow("invalid_clip_frame_index");
    expect(api.jpeg).not.toHaveBeenCalled();
    await runtime.close();
  });
});
