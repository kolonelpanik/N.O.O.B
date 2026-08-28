import { afterEach, describe, expect, it, vi } from "vitest";
import type { GatewayApi, GatewayFrame } from "./gateway.js";
import { NoobRuntime } from "./runtime.js";

const DEVICE_ID = `noob_${"a".repeat(16)}`;
const LEASE = "b".repeat(32);
const REQUEST_ID = "12345678-1234-4123-8123-123456789abc";

function gatewayView() {
  return {
    device_id: DEVICE_ID,
    connection_id: "conn_control_expiry",
    connection_state: "connected" as const,
    connected_at: "2026-08-27T21:00:00.000Z",
    local_port: 18_765,
  };
}

function releaseCalls(post: ReturnType<typeof vi.fn>): unknown[][] {
  return post.mock.calls.filter(([path]) => path === "/api/v1/control/release");
}

async function setup(ttlMs = 10_000) {
  const runtime = new NoobRuntime();
  const frame = vi.fn(async (): Promise<GatewayFrame> => ({
    bytes: new Uint8Array([0xff, 0xd8, 0xff, 0xd9]),
    sequence: 1,
    generation: 1,
    observedAt: new Date().toISOString(),
    proofStartedAtMs: Date.now(),
  }));
  const post = vi.fn(async (path: string): Promise<object> => {
    if (path === "/api/v1/control/claim") return { lease: LEASE, ttl_ms: ttlMs };
    return { ok: true };
  });
  const api = { frame, post };
  vi.spyOn(runtime.tunnels, "api").mockReturnValue({
    api: api as unknown as GatewayApi,
    view: gatewayView(),
  });
  const observed = await runtime.frame(DEVICE_ID, "target");
  const frameToken = (observed.structured as { frame_token: string }).frame_token;
  return { runtime, api, post, frameToken };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("bounded control expiry", () => {
  it("releases an unused lease at the advertised last-used idle deadline", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 30) as {
      control_session_id: string;
      idle_timeout_ms: number;
    };

    expect(acquired.idle_timeout_ms).toBe(5_000);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(releaseCalls(post)).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1);
    expect(releaseCalls(post)).toHaveLength(1);
    await expect(runtime.releaseControl(acquired.control_session_id)).rejects.toThrow(
      "control_session_unknown",
    );
    await runtime.close();
  });

  it("moves the idle deadline after a completed HID action", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 30) as {
      control_session_id: string;
    };

    await vi.advanceTimersByTimeAsync(4_000);
    await runtime.typeText(
      acquired.control_session_id,
      "safe test",
      0,
      frameToken,
      REQUEST_ID,
    );
    await vi.advanceTimersByTimeAsync(4_999);
    expect(releaseCalls(post)).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1);
    expect(releaseCalls(post)).toHaveLength(1);
    await runtime.close();
  });

  it("counts a completed drag as activity before restarting the idle clock", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 30) as {
      control_session_id: string;
    };

    await vi.advanceTimersByTimeAsync(4_000);
    const drag = runtime.drag(
      acquired.control_session_id,
      "left",
      [{ dx: 5, dy: 5, duration_ms: 10 }],
      frameToken,
      REQUEST_ID,
    );
    await vi.advanceTimersByTimeAsync(10);
    await drag;
    await vi.advanceTimersByTimeAsync(4_999);
    expect(releaseCalls(post)).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(releaseCalls(post)).toHaveLength(1);
    await runtime.close();
  });

  it("does not classify a long in-flight action as idle", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, api, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 30) as {
      control_session_id: string;
    };
    let finishInput: ((value: object) => void) | undefined;
    const pendingInput = new Promise<object>((resolve) => { finishInput = resolve; });
    api.post.mockImplementation(async (path: string): Promise<object> => {
      if (path === "/api/v1/control/claim") return { lease: LEASE, ttl_ms: 10_000 };
      if (path === "/api/v1/input") return await pendingInput;
      return { ok: true };
    });

    await vi.advanceTimersByTimeAsync(4_000);
    const input = runtime.typeText(
      acquired.control_session_id,
      "bounded long action",
      25,
      frameToken,
      REQUEST_ID,
    );
    await vi.advanceTimersByTimeAsync(4_000);
    expect(releaseCalls(post)).toHaveLength(0);

    finishInput?.({ ok: true });
    await input;
    await vi.advanceTimersByTimeAsync(4_999);
    expect(releaseCalls(post)).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(releaseCalls(post)).toHaveLength(1);
    await runtime.close();
  });

  it("keeps the absolute deadline authoritative after recent activity", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 5) as {
      control_session_id: string;
    };

    await vi.advanceTimersByTimeAsync(4_000);
    await runtime.typeText(
      acquired.control_session_id,
      "still bounded",
      0,
      frameToken,
      REQUEST_ID,
    );
    await vi.advanceTimersByTimeAsync(999);
    expect(releaseCalls(post)).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(releaseCalls(post)).toHaveLength(1);
    await expect(runtime.releaseControl(acquired.control_session_id)).rejects.toThrow(
      "control_session_unknown",
    );
    await runtime.close();
  });

  it("closes local authority when the best-effort idle release is unreachable", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-27T21:00:00.000Z"));
    const { runtime, api, post, frameToken } = await setup();
    const acquired = await runtime.acquireControl(DEVICE_ID, frameToken, 30) as {
      control_session_id: string;
    };
    api.post.mockImplementation(async (path: string): Promise<object> => {
      if (path === "/api/v1/control/release") throw new Error("unreachable");
      return { ok: true };
    });

    await vi.advanceTimersByTimeAsync(5_000);
    expect(releaseCalls(post)).toHaveLength(1);
    await expect(runtime.releaseControl(acquired.control_session_id)).rejects.toThrow(
      "control_session_unknown",
    );
    const renewalsAfterExpiry = post.mock.calls.filter(
      ([path]) => path === "/api/v1/control/renew",
    ).length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(post.mock.calls.filter(
      ([path]) => path === "/api/v1/control/renew",
    )).toHaveLength(renewalsAfterExpiry);
    await runtime.close();
  });
});
