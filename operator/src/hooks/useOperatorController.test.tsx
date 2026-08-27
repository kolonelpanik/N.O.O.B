import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  GatewayLocalInputResult,
  GatewayStatus,
  LocalInputStatus,
  NoobBridge,
} from "../../shared/gateway-contract";
import { useOperatorController } from "./useOperatorController";

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

function gatewayStatus(localInput: LocalInputStatus = LOCAL_READY): GatewayStatus {
  return {
    ok: true,
    serial: {
      ready: true,
      device: "/dev/noob-uart",
      firmware: "1.0",
      last_ack_age_ms: 5,
      reconnects: 0,
      last_error: null,
    },
    video: {
      state: "ready",
      generation: 4,
      active_mode_id: "hd",
      requested: {
        id: "hd",
        label: "HD",
        width: 1280,
        height: 720,
        fps: 30,
        pixel_format: "MJPG",
        max_frame_bytes: 2_097_152,
      },
      negotiated: { width: 1280, height: 720, fps: 30, pixel_format: "MJPG" },
      source_timing_detectable: false,
      ready: true,
      device: "/dev/noob-video",
      width: 1280,
      height: 720,
      fps: 15,
      last_frame_age_ms: 5,
      sequence: 1,
      restarts: 0,
      viewers: 1,
      last_error: null,
    },
    local_input: localInput,
    control: { active: false, expires_in_ms: 0, release_required: false },
  };
}

function localResult(localInput: LocalInputStatus): GatewayLocalInputResult {
  return { ok: true, local_input: localInput };
}

function mockBridge(initialStatus: GatewayStatus = gatewayStatus()): NoobBridge {
  return {
    getConfig: vi.fn(async () => ({
      gatewayUrl: "http://127.0.0.1:18765",
      gatewayLabel: "Test uConsole",
      streamUrl: "noob://gateway/stream",
      tokenConfigured: true,
    })),
    bootstrapToken: vi.fn(async () => initialStatus),
    clearToken: vi.fn(async () => undefined),
    getStatus: vi.fn(async () => initialStatus),
    getFrame: vi.fn(async () => ({
      bytes: new Uint8Array(),
      contentType: "image/jpeg" as const,
      sequence: null,
    })),
    getVideoModes: vi.fn(async () => ({
      ok: true as const,
      generation: 4,
      active_mode_id: "hd",
      requested: gatewayStatus().video.requested,
      negotiated: gatewayStatus().video.negotiated,
      state: "ready" as const,
      modes: [
        {
          id: "hd",
          label: "HD",
          width: 1280,
          height: 720,
          fps: 30,
          pixel_format: "MJPG",
          max_frame_bytes: 2_097_152,
          validated: true,
        },
        {
          id: "unvalidated",
          label: "Unvalidated",
          width: 2560,
          height: 1600,
          fps: 30,
          pixel_format: "MJPG",
          max_frame_bytes: 8_388_608,
          validated: false,
        },
        {
          id: "full-hd",
          label: "Full HD",
          width: 1920,
          height: 1080,
          fps: 30,
          pixel_format: "MJPG",
          max_frame_bytes: 4_194_304,
          validated: true,
        },
      ],
    })),
    setVideoMode: vi.fn(async () => ({
      ok: true as const,
      video: {
        ...gatewayStatus().video,
        generation: 5,
        active_mode_id: "full-hd",
        requested: {
          id: "full-hd",
          label: "Full HD",
          width: 1920,
          height: 1080,
          fps: 30,
          pixel_format: "MJPG",
          max_frame_bytes: 4_194_304,
        },
        negotiated: { width: 1920, height: 1080, fps: 30, pixel_format: "MJPG" },
        width: 1920,
        height: 1080,
        fps: 30,
      },
    })),
    claimControl: vi.fn(async () => ({ ok: true as const, ttlMs: 5_000 })),
    renewControl: vi.fn(async () => ({ ok: true as const, ttlMs: 5_000 })),
    releaseControl: vi.fn(async () => ({ ok: true as const, released: true })),
    sendInput: vi.fn(async () => ({ ok: true as const })),
    releaseAll: vi.fn(async () => ({ ok: true as const, released: true })),
    armLocalInput: vi.fn(async () => localResult({
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    })),
    disarmLocalInput: vi.fn(async () => localResult(LOCAL_READY)),
    onControlLost: vi.fn(() => () => undefined),
  };
}

async function renderController(bridge: NoobBridge) {
  Object.defineProperty(window, "noob", {
    configurable: true,
    writable: true,
    value: bridge,
  });
  const rendered = renderHook(() => useOperatorController());
  await waitFor(() => expect(rendered.result.current.connection).toBe("live"));
  return rendered;
}

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(window, "noob");
  vi.restoreAllMocks();
});

describe("operator local-input ownership", () => {
  it("merges authoritative arm and disarm snapshots immediately", async () => {
    const bridge = mockBridge();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.armLocalInput());
    expect(bridge.armLocalInput).toHaveBeenCalledOnce();
    expect(result.current.status?.local_input).toMatchObject({
      armed: true,
      exclusive_grab: true,
    });
    expect(result.current.lastAction).toBe("uConsole input armed");

    await act(() => result.current.disarmLocalInput());
    expect(bridge.disarmLocalInput).toHaveBeenCalledOnce();
    expect(result.current.status?.local_input).toMatchObject({
      armed: false,
      exclusive_grab: false,
    });
    expect(result.current.lastAction).toBe("uConsole input disarmed");
    unmount();
  });

  it("does not arm local input while this Electron session owns control", async () => {
    const bridge = mockBridge();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.claimControl());
    expect(result.current.claimed).toBe(true);
    await act(() => result.current.armLocalInput());
    expect(bridge.armLocalInput).not.toHaveBeenCalled();
    unmount();
  });

  it("blocks Electron claim while local input is armed or exclusively grabbed", async () => {
    const bridge = mockBridge(gatewayStatus({
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    }));
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.claimControl());
    expect(bridge.claimControl).not.toHaveBeenCalled();
    expect(result.current.claimed).toBe(false);
    unmount();
  });

  it("serializes same-tick local arm and Electron claim attempts", async () => {
    let resolveArm!: (value: GatewayLocalInputResult) => void;
    const bridge = mockBridge();
    vi.mocked(bridge.armLocalInput).mockReturnValueOnce(new Promise((resolve) => {
      resolveArm = resolve;
    }));
    const { result, unmount } = await renderController(bridge);

    let armPromise!: Promise<void>;
    let claimPromise!: Promise<void>;
    act(() => {
      armPromise = result.current.armLocalInput();
      claimPromise = result.current.claimControl();
    });
    expect(bridge.armLocalInput).toHaveBeenCalledOnce();
    expect(bridge.claimControl).not.toHaveBeenCalled();

    await act(async () => {
      resolveArm(localResult({
        ...LOCAL_READY,
        armed: true,
        exclusive_grab: true,
        disarm_reason: null,
      }));
      await Promise.all([armPromise, claimPromise]);
    });
    expect(result.current.status?.local_input.armed).toBe(true);
    unmount();
  });

  it("does not let an older in-flight status poll overwrite a successful arm", async () => {
    let resolvePoll!: (value: GatewayStatus) => void;
    const bridge = mockBridge();
    vi.mocked(bridge.getStatus)
      .mockResolvedValueOnce(gatewayStatus())
      .mockReturnValueOnce(new Promise((resolve) => {
        resolvePoll = resolve;
      }));
    const { result, unmount } = await renderController(bridge);

    await waitFor(() => expect(bridge.getStatus).toHaveBeenCalledTimes(2), { timeout: 1_500 });
    await act(() => result.current.armLocalInput());
    expect(result.current.status?.local_input.armed).toBe(true);

    await act(async () => {
      resolvePoll(gatewayStatus());
      await Promise.resolve();
    });
    expect(result.current.status?.local_input.armed).toBe(true);
    unmount();
  });

  it("fails closed to authentication when a local-input action loses authorization", async () => {
    const bridge = mockBridge();
    vi.mocked(bridge.armLocalInput).mockRejectedValueOnce(
      new Error('Error invoking remote method: {"code":"token_required","status":401}'),
    );
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.armLocalInput());

    expect(result.current.authenticated).toBe(false);
    expect(result.current.connection).toBe("unauthenticated");
    expect(result.current.status).toBeNull();
    expect(result.current.authDialogOpen).toBe(true);
    unmount();
  });

  it("compensates a stale arm with local disarm without disturbing another controller", async () => {
    let resolveArm!: (value: GatewayLocalInputResult) => void;
    const bridge = mockBridge();
    vi.mocked(bridge.armLocalInput).mockReturnValueOnce(new Promise((resolve) => {
      resolveArm = resolve;
    }));
    const { result, unmount } = await renderController(bridge);

    let armPromise!: Promise<void>;
    act(() => {
      armPromise = result.current.armLocalInput();
    });
    await act(() => result.current.clearToken());
    await act(async () => {
      resolveArm(localResult({
        ...LOCAL_READY,
        armed: true,
        exclusive_grab: true,
        disarm_reason: null,
      }));
      await armPromise;
    });

    expect(bridge.disarmLocalInput).toHaveBeenCalledOnce();
    expect(bridge.releaseAll).not.toHaveBeenCalled();
    unmount();
  });
});

describe("operator capture output", () => {
  it("loads validated profiles only and refreshes the private stream generation after a switch", async () => {
    const bridge = mockBridge();
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("full-hd"));

    expect(bridge.setVideoMode).toHaveBeenCalledWith("full-hd", 4);
    expect(result.current.streamGeneration).toBe(5);
    expect(result.current.status?.video.active_mode_id).toBe("full-hd");
    expect(result.current.lastAction).toBe("Capture output set to Full HD");
    unmount();
  });

  it("blocks capture-output changes while remote or local input owns control", async () => {
    const remoteStatus = {
      ...gatewayStatus(),
      control: { active: true, expires_in_ms: 4_000, release_required: false },
    };
    const bridge = mockBridge(remoteStatus);
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("full-hd"));

    expect(bridge.setVideoMode).not.toHaveBeenCalled();
    unmount();
  });

  it("never sends an unvalidated profile", async () => {
    const bridge = mockBridge();
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("unvalidated"));

    expect(bridge.setVideoMode).not.toHaveBeenCalled();
    unmount();
  });

  it("can switch away from a degraded capture while input ownership is idle", async () => {
    const degraded = gatewayStatus();
    degraded.video = { ...degraded.video, state: "degraded", ready: false };
    const bridge = mockBridge(degraded);
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("full-hd"));

    expect(bridge.setVideoMode).toHaveBeenCalledWith("full-hd", 4);
    expect(result.current.streamGeneration).toBe(5);
    expect(result.current.status?.video.active_mode_id).toBe("full-hd");
    unmount();
  });

  it("blocks capture-output changes while a rollback transition is in progress", async () => {
    const rollingBack = gatewayStatus();
    rollingBack.video = { ...rollingBack.video, state: "rolling_back", ready: true };
    const bridge = mockBridge(rollingBack);
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("full-hd"));

    expect(bridge.setVideoMode).not.toHaveBeenCalled();
    unmount();
  });

  it("surfaces an ambiguous mode timeout as unconfirmed without replaying", async () => {
    const bridge = mockBridge();
    vi.mocked(bridge.setVideoMode).mockRejectedValueOnce(
      new Error('Error invoking remote method: {"code":"video_mode_unconfirmed","status":null}'),
    );
    const { result, unmount } = await renderController(bridge);
    await waitFor(() => expect(result.current.videoModes).toHaveLength(2));

    await act(() => result.current.switchVideoMode("full-hd"));

    expect(bridge.setVideoMode).toHaveBeenCalledOnce();
    expect(result.current.videoModeError).toBe("video_mode_unconfirmed");
    expect(result.current.lastAction).toBe("Capture output unconfirmed");
    unmount();
  });
});
