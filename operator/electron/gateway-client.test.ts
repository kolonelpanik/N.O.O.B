import { afterEach, describe, expect, it, vi } from "vitest";
import type { GatewayLocalInputResult, LocalInputStatus } from "../shared/gateway-contract.js";
import { GatewayClient } from "./gateway-client.js";

const TOKEN = "t".repeat(32);
const LEASE = "a".repeat(32);

const LOCAL_READY: LocalInputStatus = {
  enabled: true,
  ready: true,
  armed: false,
  exclusive_grab: false,
  keyboard_ready: true,
  pointer_ready: true,
  last_event_age_ms: null,
  last_error: null,
  disarm_reason: "operator",
  dropped_events: 0,
};

function jsonResponse(payload: object, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("GatewayClient local input", () => {
  it("uses authenticated empty POSTs without attaching or clearing the Electron lease", async () => {
    const armed: GatewayLocalInputResult = {
      ok: true,
      local_input: { ...LOCAL_READY, armed: true, exclusive_grab: true, disarm_reason: null },
    };
    const disarmed: GatewayLocalInputResult = { ok: true, local_input: LOCAL_READY };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true, lease: LEASE, ttl_ms: 5_000 }))
      .mockResolvedValueOnce(jsonResponse(armed))
      .mockResolvedValueOnce(jsonResponse(disarmed))
      .mockResolvedValueOnce(jsonResponse({ ok: true, result: { ack: true } }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);
    await client.claim();
    await expect(client.armLocalInput()).resolves.toEqual(armed);
    await expect(client.disarmLocalInput()).resolves.toEqual(disarmed);
    await client.input({ op: "ping" });

    const armRequest = fetchMock.mock.calls[1] as [string, RequestInit];
    const disarmRequest = fetchMock.mock.calls[2] as [string, RequestInit];
    for (const [url, init] of [armRequest, disarmRequest]) {
      expect(url).toMatch(/\/api\/v1\/local-input\/(arm|disarm)$/);
      expect(init.method).toBe("POST");
      expect(init.body).toBe("{}");
      const headers = new Headers(init.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
      expect(headers.has("X-NOOB-Lease")).toBe(false);
    }

    const inputHeaders = new Headers((fetchMock.mock.calls[3] as [string, RequestInit])[1].headers);
    expect(inputHeaders.get("X-NOOB-Lease")).toBe(LEASE);
  });

  it("returns a bounded public domain error without exposing the response body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse({ ok: false, error: "lease_busy", detail: TOKEN }, 409),
    ));
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    const error = await client.armLocalInput().catch((caught: unknown) => caught);
    expect(error).toMatchObject({
      publicError: { code: "lease_busy", status: 409 },
    });
    expect(String(error)).not.toContain(TOKEN);
  });
});

describe("GatewayClient capture output", () => {
  it("lists profiles and sends a bounded optimistic-generation switch request without a lease", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        generation: 7,
        active_mode_id: "hd",
        requested: null,
        negotiated: null,
        state: "ready",
        modes: [],
      }))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        video: { generation: 8, active_mode_id: "full-hd" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await client.videoModes();
    await client.setVideoMode("full-hd", 7);

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:18765/api/v1/video/modes");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18765/api/v1/video/mode");
    expect(JSON.parse(String(init.body))).toEqual({
      mode_id: "full-hd",
      expected_generation: 7,
    });
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(headers.has("X-NOOB-Lease")).toBe(false);
  });

  it("rejects malformed mode IDs and generations before network I/O", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await expect(client.setVideoMode("../unsafe", 1)).rejects.toMatchObject({
      publicError: { code: "invalid_video_mode_request" },
    });
    await expect(client.setVideoMode("hd", -1)).rejects.toMatchObject({
      publicError: { code: "invalid_video_mode_request" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("allows a legitimate mode transaction to complete beyond the ordinary four-second deadline", async () => {
    vi.useFakeTimers();
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), milliseconds);
      return controller.signal;
    });
    const fetchMock = vi.fn((_url: string | URL | Request, init?: RequestInit) =>
      new Promise<Response>((resolve, reject) => {
        const timer = setTimeout(() => resolve(jsonResponse({
          ok: true,
          video: { generation: 8, active_mode_id: "full-hd" },
        })), 5_000);
        init?.signal?.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new DOMException("aborted", "AbortError"));
        }, { once: true });
      }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    const operation = client.setVideoMode("full-hd", 7);
    await vi.advanceTimersByTimeAsync(5_001);

    await expect(operation).resolves.toMatchObject({
      ok: true,
      video: { generation: 8, active_mode_id: "full-hd" },
    });
    expect(timeoutSpy).toHaveBeenCalledWith(65_000);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("classifies the bounded mode timeout as unconfirmed without replaying", async () => {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), milliseconds);
      return controller.signal;
    });
    const fetchMock = vi.fn((_url: string | URL | Request, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        }, { once: true });
      }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    const operation = client.setVideoMode("full-hd", 7);
    const rejection = expect(operation).rejects.toMatchObject({
      publicError: { code: "video_mode_unconfirmed", status: null },
    });
    await vi.advanceTimersByTimeAsync(65_001);

    await rejection;
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe("GatewayClient environmental camera", () => {
  it.each([
    ["target", "/api/v1/frame.jpg", 4_000],
    ["environment", "/api/v1/environment-camera/frame.jpg", 35_000],
  ] as const)("uses the bounded %s frame deadline", async (source, path, timeoutMs) => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockImplementation(() => new AbortController().signal);
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([0xff, 0xd8, 0xff, 0xd9]), {
      headers: { "Content-Type": "image/jpeg" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await client.frame(source);

    expect(timeoutSpy).toHaveBeenCalledOnce();
    expect(timeoutSpy).toHaveBeenCalledWith(timeoutMs);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe(`http://127.0.0.1:18765${path}`);
  });

  it("uses only the bounded generation-checked mutation envelopes and never attaches a control lease", async () => {
    const mediaId = `m_${"b".repeat(32)}`;
    const jobId = `j_${"c".repeat(32)}`;
    const camera = {
      configured: true,
      reachable: true,
      device_id: `cam_${"d".repeat(16)}`,
      stream_enabled: true,
      sensor_enabled: true,
      sensor_initialized: true,
      power_control: false,
      frame_ready: true,
      generation: 8,
      sequence: 4,
      width: 640,
      height: 480,
      last_frame_age_ms: 9,
      viewers: 1,
      storage: {
        state: "mounted",
        mounted: true,
        writable: true,
        total_bytes: 1024,
        free_bytes: 512,
        reserve_bytes: 64,
        media_count: 1,
        active_job_id: null,
        limits: {
          max_media_items: 50,
          max_total_bytes: 1024,
          max_clip_duration_ms: 30_000,
          max_clip_fps: 5,
          max_clip_frames: 150,
        },
        last_error: null,
      },
      last_error: null,
    } as const;
    const item = {
      id: mediaId,
      kind: "snapshot",
      state: "complete",
      created_at: null,
      created_uptime_ms: 10,
      size_bytes: 100,
      width: 640,
      height: 480,
      frame_count: 1,
      fps: null,
      duration_ms: 0,
      content_type: "image/jpeg",
    } as const;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true, lease: LEASE, ttl_ms: 5_000 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, environment_camera: camera }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, item }, 201))
      .mockResolvedValueOnce(jsonResponse({ ok: true, job_id: jobId, state: "queued" }, 202))
      .mockResolvedValueOnce(jsonResponse({
        ok: true,
        job: {
          job_id: jobId,
          kind: "clip",
          state: "running",
          created_uptime_ms: 12,
          frames_written: 2,
          frames_target: 15,
          media_id: null,
          error_code: null,
        },
      }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, job_id: jobId, state: "cancelling" }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);
    await client.claim();

    await client.setEnvironmentCamera(true, 7);
    await client.captureEnvironmentSnapshot(8);
    await client.startEnvironmentClip(5, 3, 8);
    await client.getEnvironmentClipJob(jobId);
    await client.stopEnvironmentClip(jobId);

    const expected = [
      ["/api/v1/environment-camera/state", { enabled: true, expected_generation: 7 }],
      ["/api/v1/environment-camera/snapshot", { expected_generation: 8 }],
      ["/api/v1/environment-camera/clip", { duration_seconds: 5, fps: 3, expected_generation: 8 }],
      [`/api/v1/environment-camera/jobs/${jobId}`, null],
      [`/api/v1/environment-camera/jobs/${jobId}/stop`, {}],
    ] as const;
    expected.forEach(([path, body], index) => {
      const [url, init] = fetchMock.mock.calls[index + 1] as [string, RequestInit];
      expect(url).toBe(`http://127.0.0.1:18765${path}`);
      expect(body === null ? init.body : JSON.parse(String(init.body))).toEqual(body === null ? undefined : body);
      const headers = new Headers(init.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
      expect(headers.has("X-NOOB-Lease")).toBe(false);
    });
  });

  it("bounds media IDs, frame indexes, cursors, clip duration, and clip frame count before network I/O", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await expect(client.environmentMediaContent("../secret")).rejects.toMatchObject({
      publicError: { code: "invalid_media_id" },
    });
    await expect(client.environmentMediaFrame(`m_${"a".repeat(32)}`, 150)).rejects.toMatchObject({
      publicError: { code: "invalid_environment_media_frame" },
    });
    await expect(client.listEnvironmentMedia(20, "../../bad")).rejects.toMatchObject({
      publicError: { code: "invalid_environment_media_request" },
    });
    await expect(client.startEnvironmentClip(31, 1, 2)).rejects.toMatchObject({
      publicError: { code: "invalid_environment_clip_request" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("proxies only validated snapshot and clip-frame storage paths", async () => {
    const mediaId = `m_${"f".repeat(32)}`;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(new Uint8Array([0xff, 0xd8, 0xff, 0xd9]), {
        headers: { "Content-Type": "image/jpeg" },
      }))
      .mockResolvedValueOnce(new Response(new Uint8Array([0xff, 0xd8, 0xff, 0xd9]), {
        headers: { "Content-Type": "image/jpeg" },
      }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await client.environmentMediaContent(mediaId);
    await client.environmentMediaFrame(mediaId, 0);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `http://127.0.0.1:18765/api/v1/environment-camera/storage/${mediaId}/content`,
      `http://127.0.0.1:18765/api/v1/environment-camera/storage/${mediaId}/frames/0.jpg`,
    ]);
  });
});

describe("GatewayClient lease ownership", () => {
  it("exposes ownership without exposing the lease value and releases through the scoped endpoint", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true, lease: LEASE, ttl_ms: 5_000 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, released: true }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    expect(client.hasLease).toBe(false);
    await client.claim();
    expect(client.hasLease).toBe(true);
    await client.release();
    expect(client.hasLease).toBe(false);

    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:18765/api/v1/control/release");
    expect(new Headers(init.headers).get("X-NOOB-Lease")).toBe(LEASE);
  });

  it("clears local ownership when the bearer token is cleared", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ok: true, lease: LEASE, ttl_ms: 5_000 })),
    );
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);
    await client.claim();

    client.clearToken();

    expect(client.tokenConfigured).toBe(false);
    expect(client.hasLease).toBe(false);
  });

  it("keeps the explicit emergency release global even without an owned lease", async () => {
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ ok: true, released: true }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new GatewayClient("http://127.0.0.1:18765");
    client.setToken(TOKEN);

    await client.releaseAll();

    expect(client.hasLease).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:18765/api/v1/release-all",
    );
  });
});
