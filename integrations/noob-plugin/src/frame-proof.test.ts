import { afterEach, describe, expect, it, vi } from "vitest";
import { GatewayApi, type GatewayFrame } from "./gateway.js";
import { NoobRuntime } from "./runtime.js";

const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);
const DEVICE_ID = `noob_${"a".repeat(16)}`;

function frameResponse(headers: Record<string, string>): Response {
  return new Response(JPEG, {
    status: 200,
    headers: {
      "content-type": "image/jpeg",
      "x-noob-frame-sequence": "41",
      ...headers,
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("frame proof binding", () => {
  it.each([
    ["target", "x-noob-video-generation"],
    ["environment", "x-noob-environment-generation"],
  ] as const)("binds %s proof to its exact response generation header", async (source, header) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    vi.stubGlobal("fetch", vi.fn(async () => frameResponse({ [header]: "17" })));

    const frame = await new GatewayApi("http://127.0.0.1:8765", "t".repeat(48)).frame(source);

    expect(frame).toMatchObject({
      sequence: 41,
      generation: 17,
      observedAt: "2026-08-27T21:00:00.000Z",
      proofStartedAtMs: Date.parse("2026-08-27T21:00:00.000Z"),
    });
  });

  it.each([
    ["target", {}],
    ["environment", { "x-noob-video-generation": "7" }],
    ["target", { "x-noob-video-generation": "7suffix" }],
  ] as const)("fails closed when %s response lacks a valid source generation", async (source, headers) => {
    vi.stubGlobal("fetch", vi.fn(async () => frameResponse(headers)));
    const api = new GatewayApi("http://127.0.0.1:8765", "t".repeat(48));

    await expect(api.frame(source)).rejects.toThrow("invalid_frame_proof");
  });

  it("uses the frame header generation without a separate status race", async () => {
    const runtime = new NoobRuntime();
    const frame: GatewayFrame = {
      bytes: JPEG,
      sequence: 52,
      generation: 23,
      observedAt: "2026-08-27T21:00:01.000Z",
      proofStartedAtMs: Date.now(),
    };
    const api = {
      frame: vi.fn(async () => frame),
      json: vi.fn(async () => ({ video: { generation: 999 } })),
    };
    vi.spyOn(runtime.tunnels, "api").mockReturnValue({
      api: api as unknown as GatewayApi,
      view: {
        device_id: DEVICE_ID,
        connection_id: "conn_test",
        connection_state: "connected",
        connected_at: "2026-08-27T21:00:00.000Z",
        local_port: 18765,
      },
    });

    const result = await runtime.frame(DEVICE_ID, "target");
    const structured = result.structured as Record<string, unknown>;

    expect(api.json).not.toHaveBeenCalled();
    expect(structured).toMatchObject({
      generation: 23,
      sequence: 52,
      observed_at: frame.observedAt,
      capture_time_known: false,
      freshness_basis: "gateway_ready_at_response",
      stale: false,
    });
    expect(structured).not.toHaveProperty("captured_at");
    await runtime.close();
  });
});
