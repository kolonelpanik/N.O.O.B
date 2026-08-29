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
      connectionMode: "fixed" as const,
      currentDeviceId: null,
    })),
    listDevices: vi.fn(async () => ({ devices: [], currentDeviceId: null })),
    discoverDevices: vi.fn(async () => ({ candidates: [] })),
    probeDevice: vi.fn(async () => {
      throw new Error("device discovery not configured in this controller test");
    }),
    inspectDevice: vi.fn(async () => {
      throw new Error("device discovery not configured in this controller test");
    }),
    pairAndConnectDevice: vi.fn(async () => {
      throw new Error("device discovery not configured in this controller test");
    }),
    connectKnownDevice: vi.fn(async () => {
      throw new Error("device discovery not configured in this controller test");
    }),
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
    setEnvironmentCamera: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
    captureEnvironmentSnapshot: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
    listEnvironmentMedia: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
    startEnvironmentClip: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
    getEnvironmentClipJob: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
    stopEnvironmentClip: vi.fn(async () => {
      throw new Error("environment camera not configured in this controller test");
    }),
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
  Reflect.deleteProperty(document, "pointerLockElement");
  Reflect.deleteProperty(document, "exitPointerLock");
  vi.restoreAllMocks();
});

function installPointerLockHarness() {
  let lockedElement: Element | null = null;
  const target = document.createElement("div");
  const requestPointerLock = vi.fn(async () => {
    lockedElement = target;
    document.dispatchEvent(new Event("pointerlockchange"));
  });
  const exitPointerLock = vi.fn(() => {
    lockedElement = null;
    document.dispatchEvent(new Event("pointerlockchange"));
  });
  Object.defineProperty(target, "requestPointerLock", {
    configurable: true,
    value: requestPointerLock,
  });
  Object.defineProperty(document, "pointerLockElement", {
    configurable: true,
    get: () => lockedElement,
  });
  Object.defineProperty(document, "exitPointerLock", {
    configurable: true,
    value: exitPointerLock,
  });

  return { target, requestPointerLock, exitPointerLock };
}

describe("one-click direct control", () => {
  it("requests pointer lock in the trusted click turn, then enables human keyboard and pointer capture after claim", async () => {
    let resolveClaim!: (value: { ok: true; ttlMs: number }) => void;
    const bridge = mockBridge();
    vi.mocked(bridge.claimControl).mockReturnValueOnce(new Promise((resolve) => {
      resolveClaim = resolve;
    }));
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.setMode("agent"));
    let takePromise!: Promise<void>;
    act(() => {
      takePromise = result.current.takeDirectControl(pointerLock.target);
    });

    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    expect(bridge.claimControl).not.toHaveBeenCalled();

    await act(async () => {
      await Promise.resolve();
    });

    expect(bridge.claimControl).toHaveBeenCalledOnce();
    expect(pointerLock.requestPointerLock.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(bridge.claimControl).mock.invocationCallOrder[0],
    );
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);

    await act(async () => {
      resolveClaim({ ok: true, ttlMs: 5_000 });
      await takePromise;
    });

    expect(result.current.claimed).toBe(true);
    expect(result.current.mode).toBe("human");
    expect(result.current.keyboardCapture).toBe(true);
    expect(result.current.pointerCapture).toBe(true);
    expect(result.current.pointerLocked).toBe(true);
    unmount();
  });

  it("releases a provisional pointer lock and leaves capture off when the gateway claim fails", async () => {
    const bridge = mockBridge();
    vi.mocked(bridge.claimControl).mockRejectedValueOnce(new Error("control unavailable"));
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.takeDirectControl(pointerLock.target));

    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    expect(pointerLock.exitPointerLock).toHaveBeenCalledOnce();
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    expect(result.current.lastAction).toBe("Control unavailable");
    unmount();
  });

  it("clears automatic keyboard and pointer capture on explicit release", async () => {
    const bridge = mockBridge();
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.takeDirectControl(pointerLock.target));
    expect(result.current.keyboardCapture).toBe(true);
    expect(result.current.pointerCapture).toBe(true);

    await act(() => result.current.releaseControl());

    expect(bridge.releaseControl).toHaveBeenCalledOnce();
    expect(pointerLock.exitPointerLock).toHaveBeenCalledOnce();
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    unmount();
  });

  it("clears automatic keyboard and pointer capture when the operator window loses focus", async () => {
    const bridge = mockBridge();
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.takeDirectControl(pointerLock.target));
    expect(result.current.keyboardCapture).toBe(true);
    expect(result.current.pointerCapture).toBe(true);

    act(() => window.dispatchEvent(new Event("blur")));

    expect(pointerLock.exitPointerLock).toHaveBeenCalledOnce();
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    unmount();
  });

  it("disarms authoritative local ownership before claiming direct control", async () => {
    let resolveDisarm!: (value: GatewayLocalInputResult) => void;
    const armed = {
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    };
    const bridge = mockBridge(gatewayStatus(armed));
    vi.mocked(bridge.disarmLocalInput).mockReturnValueOnce(new Promise((resolve) => {
      resolveDisarm = resolve;
    }));
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    let takePromise!: Promise<void>;
    act(() => {
      takePromise = result.current.takeDirectControl(pointerLock.target);
    });

    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    await act(async () => {
      await Promise.resolve();
    });
    expect(bridge.disarmLocalInput).toHaveBeenCalledOnce();
    expect(bridge.claimControl).not.toHaveBeenCalled();

    await act(async () => {
      resolveDisarm(localResult(LOCAL_READY));
      await takePromise;
    });

    expect(bridge.claimControl).toHaveBeenCalledOnce();
    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    expect(vi.mocked(bridge.disarmLocalInput).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(bridge.claimControl).mock.invocationCallOrder[0],
    );
    expect(result.current.status?.local_input).toMatchObject({
      armed: false,
      exclusive_grab: false,
    });
    expect(result.current.claimed).toBe(true);
    expect(result.current.mode).toBe("human");
    expect(result.current.keyboardCapture).toBe(true);
    expect(result.current.pointerCapture).toBe(true);
    expect(result.current.pointerLocked).toBe(true);
    unmount();
  });

  it("leaves local ownership untouched when the trusted pointer-lock request is denied", async () => {
    const armed = {
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    };
    const bridge = mockBridge(gatewayStatus(armed));
    const pointerLock = installPointerLockHarness();
    pointerLock.requestPointerLock.mockRejectedValueOnce(new Error("pointer lock denied"));
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.takeDirectControl(pointerLock.target));

    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    expect(bridge.disarmLocalInput).not.toHaveBeenCalled();
    expect(bridge.claimControl).not.toHaveBeenCalled();
    expect(bridge.releaseControl).not.toHaveBeenCalled();
    expect(result.current.status?.local_input).toMatchObject({
      armed: true,
      exclusive_grab: true,
    });
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    expect(result.current.lastAction).toBe("Pointer lock unavailable");
    unmount();
  });

  it("stops before remote claim when the authoritative local disarm remains grabbed", async () => {
    const armed = {
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    };
    const bridge = mockBridge(gatewayStatus(armed));
    vi.mocked(bridge.disarmLocalInput).mockResolvedValueOnce(localResult(armed));
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    await act(() => result.current.takeDirectControl(pointerLock.target));

    expect(pointerLock.requestPointerLock).toHaveBeenCalledOnce();
    expect(bridge.disarmLocalInput).toHaveBeenCalledOnce();
    expect(bridge.claimControl).not.toHaveBeenCalled();
    expect(bridge.releaseControl).not.toHaveBeenCalled();
    expect(pointerLock.exitPointerLock).toHaveBeenCalledOnce();
    expect(result.current.status?.local_input).toMatchObject({
      armed: true,
      exclusive_grab: true,
    });
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    expect(result.current.localInputError).toBe("local_input_disarm_unconfirmed");
    expect(result.current.lastAction).toBe("uConsole disarm unconfirmed");
    unmount();
  });

  it("releases a late remote lease when the operator window blurs during claim", async () => {
    let resolveClaim!: (value: { ok: true; ttlMs: number }) => void;
    const bridge = mockBridge();
    vi.mocked(bridge.claimControl).mockReturnValueOnce(new Promise((resolve) => {
      resolveClaim = resolve;
    }));
    const pointerLock = installPointerLockHarness();
    const { result, unmount } = await renderController(bridge);

    let takePromise!: Promise<void>;
    act(() => {
      takePromise = result.current.takeDirectControl(pointerLock.target);
    });
    await waitFor(() => expect(bridge.claimControl).toHaveBeenCalledOnce());

    act(() => window.dispatchEvent(new Event("blur")));
    expect(pointerLock.exitPointerLock).toHaveBeenCalledOnce();
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);

    await act(async () => {
      resolveClaim({ ok: true, ttlMs: 5_000 });
      await takePromise;
    });

    expect(bridge.releaseControl).toHaveBeenCalledOnce();
    expect(result.current.claimed).toBe(false);
    expect(result.current.keyboardCapture).toBe(false);
    expect(result.current.pointerCapture).toBe(false);
    expect(result.current.pointerLocked).toBe(false);
    unmount();
  });
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

  it("does not report a local disarm until the returned device snapshot is released", async () => {
    const armed = {
      ...LOCAL_READY,
      armed: true,
      exclusive_grab: true,
      disarm_reason: null,
    };
    const bridge = mockBridge(gatewayStatus(armed));
    vi.mocked(bridge.disarmLocalInput).mockResolvedValueOnce(localResult(armed));
    const { result, unmount } = await renderController(bridge);

    let confirmed = true;
    await act(async () => {
      confirmed = await result.current.disarmLocalInput();
    });

    expect(confirmed).toBe(false);
    expect(result.current.status?.local_input).toMatchObject({
      armed: true,
      exclusive_grab: true,
    });
    expect(result.current.localInputError).toBe("local_input_disarm_unconfirmed");
    expect(result.current.lastAction).toBe("uConsole disarm unconfirmed");
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

describe("operator device adoption", () => {
  it("installs the returned connection and refreshes authenticated state in place", async () => {
    const initialStatus = gatewayStatus();
    const adoptedStatus = gatewayStatus();
    adoptedStatus.video = {
      ...adoptedStatus.video,
      generation: 11,
      device: "/dev/noob-video-adopted",
    };
    const bridge = mockBridge(initialStatus);
    vi.mocked(bridge.getStatus)
      .mockResolvedValueOnce(initialStatus)
      .mockResolvedValueOnce(adoptedStatus);
    const { result, unmount } = await renderController(bridge);
    const adoptedConfig = {
      gatewayUrl: "http://127.0.0.1:23456",
      gatewayLabel: "Paired uConsole",
      streamUrl: "noob://gateway/stream",
      tokenConfigured: true,
      connectionMode: "ssh-tunnel" as const,
      currentDeviceId: "noob_paired",
    };

    await act(() => result.current.adoptConnection(adoptedConfig));

    expect(bridge.getStatus).toHaveBeenCalledTimes(2);
    expect(result.current.config).toEqual(adoptedConfig);
    expect(result.current.authenticated).toBe(true);
    expect(result.current.connection).toBe("live");
    expect(result.current.status?.video.device).toBe("/dev/noob-video-adopted");
    expect(result.current.streamGeneration).toBe(11);
    expect(result.current.lastAction).toBe("Device route changed");
    unmount();
  });

  it("adopts an unauthenticated connection without probing it with stale credentials", async () => {
    const bridge = mockBridge();
    const { result, unmount } = await renderController(bridge);
    const callsBeforeAdoption = vi.mocked(bridge.getStatus).mock.calls.length;

    await act(() => result.current.adoptConnection({
      gatewayUrl: "http://127.0.0.1:24567",
      gatewayLabel: "Unprovisioned uConsole",
      streamUrl: "noob://gateway/stream",
      tokenConfigured: false,
      connectionMode: "ssh-tunnel",
      currentDeviceId: "noob_unprovisioned",
    }));

    expect(bridge.getStatus).toHaveBeenCalledTimes(callsBeforeAdoption);
    expect(result.current.authenticated).toBe(false);
    expect(result.current.connection).toBe("unauthenticated");
    expect(result.current.authDialogOpen).toBe(true);
    expect(result.current.status).toBeNull();
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
