import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GatewayConfigView, GatewayStatus } from "../shared/gateway-contract";
import type { OperatorController } from "./hooks/useOperatorController";
import App from "./App";

const mocks = vi.hoisted(() => ({
  useOperatorController: vi.fn(),
  adoptedConfig: {
    gatewayUrl: "http://127.0.0.1:23456",
    gatewayLabel: "Paired uConsole",
    streamUrl: "noob://gateway/stream",
    tokenConfigured: true,
    connectionMode: "ssh-tunnel",
    currentDeviceId: "noob_paired",
  },
}));

vi.mock("./hooks/useOperatorController", () => ({
  useOperatorController: mocks.useOperatorController,
}));

vi.mock("./hooks/useEnvironmentCamera", () => ({
  useEnvironmentCamera: () => ({
    camera: null,
    storage: null,
    media: [],
    nextCursor: null,
    activeJob: null,
    activeJobId: null,
    busy: false,
    mediaBusy: false,
    error: null,
    setStreaming: vi.fn(),
    captureSnapshot: vi.fn(),
    startClip: vi.fn(),
    stopClip: vi.fn(),
    refreshMedia: vi.fn(),
    loadMore: vi.fn(),
  }),
}));

vi.mock("./hooks/useFrameFeed", () => ({
  useFrameFeed: () => ({
    imageSource: null,
    usingFrameFallback: false,
    markStreamFailed: vi.fn(),
    resetStream: vi.fn(),
  }),
}));

vi.mock("./components/AppShell", () => ({
  AppShell: ({
    onDevices,
    workspace,
    controlRail,
  }: {
    onDevices(): void;
    workspace: React.ReactNode;
    controlRail: React.ReactNode;
  }) => (
    <main>
      <button type="button" onClick={onDevices}>Devices</button>
      {workspace}
      {controlRail}
    </main>
  ),
}));

vi.mock("./components/DevicePicker", () => ({
  DevicePicker: ({
    open,
    onConnected,
  }: {
    open: boolean;
    onConnected(config: GatewayConfigView): void | Promise<void>;
  }) => open ? (
    <section data-testid="device-picker">
      <button
        type="button"
        onClick={() => void onConnected(mocks.adoptedConfig as GatewayConfigView)}
      >
        Complete pair
      </button>
    </section>
  ) : null,
}));

function armedGatewayStatus(): GatewayStatus {
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
      fps: 30,
      last_frame_age_ms: 5,
      sequence: 1,
      restarts: 0,
      viewers: 1,
      last_error: null,
    },
    local_input: {
      enabled: true,
      ready: true,
      armed: true,
      exclusive_grab: true,
      keyboard_ready: true,
      pointer_ready: true,
      last_event_age_ms: 5,
      last_error: null,
      disarm_reason: null,
      dropped_events: 0,
    },
    control: { active: true, expires_in_ms: 5_000, release_required: false },
  };
}

function controller(
  adoptConnection: OperatorController["adoptConnection"],
  overrides: Partial<OperatorController> = {},
): OperatorController {
  return {
    config: {
      gatewayUrl: "http://127.0.0.1:18765",
      gatewayLabel: "Initial route",
      streamUrl: "noob://gateway/stream",
      tokenConfigured: false,
      connectionMode: "fixed",
      currentDeviceId: null,
    },
    status: null,
    connection: "unauthenticated",
    authenticated: false,
    authDialogOpen: false,
    authError: null,
    sessionStartedAt: null,
    claimed: false,
    leaseRemainingMs: null,
    mode: "human",
    keyboardCapture: false,
    pointerCapture: false,
    pointerLocked: false,
    localInputBusy: false,
    localInputError: null,
    videoModes: [],
    videoModeBusy: false,
    videoModeError: null,
    lastAction: "—",
    streamGeneration: 0,
    setAuthDialogOpen: vi.fn(),
    adoptConnection,
    bootstrapToken: vi.fn(async () => false),
    clearToken: vi.fn(async () => undefined),
    claimControl: vi.fn(async () => undefined),
    takeDirectControl: vi.fn(async () => undefined),
    releaseControl: vi.fn(async () => true),
    emergencyRelease: vi.fn(async () => undefined),
    armLocalInput: vi.fn(async () => undefined),
    disarmLocalInput: vi.fn(async () => true),
    switchVideoMode: vi.fn(async () => undefined),
    setMode: vi.fn(),
    toggleKeyboardCapture: vi.fn(),
    togglePointerCapture: vi.fn(),
    requestPointerLock: vi.fn(async () => undefined),
    sendInput: vi.fn(async () => false),
    ...overrides,
  };
}

beforeEach(() => {
  mocks.useOperatorController.mockReset();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App device adoption", () => {
  it("adopts the returned first-pair config and closes the picker without reloading the renderer", async () => {
    const adoptConnection = vi.fn(async () => undefined);
    mocks.useOperatorController.mockReturnValue(controller(adoptConnection));

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Devices" }));
    expect(screen.getByTestId("device-picker")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Complete pair" }));

    await waitFor(() => expect(adoptConnection).toHaveBeenCalledWith(mocks.adoptedConfig));
    await waitFor(() => expect(screen.queryByTestId("device-picker")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Devices" })).toBeInTheDocument();
  });
});

describe("App direct control", () => {
  it("routes Take control through the one-click direct-control path with the target element", () => {
    const takeDirectControl = vi.fn(async () => undefined);
    const claimControl = vi.fn(async () => undefined);
    const requestPointerLock = vi.fn(async () => undefined);
    mocks.useOperatorController.mockReturnValue(controller(vi.fn(async () => undefined), {
      authenticated: true,
      connection: "live",
      takeDirectControl,
      claimControl,
      requestPointerLock,
    }));

    render(<App />);
    const target = screen.getByLabelText("Target display");
    fireEvent.click(screen.getByRole("button", { name: "Take control" }));

    expect(takeDirectControl).toHaveBeenCalledOnce();
    expect(takeDirectControl).toHaveBeenCalledWith(target);
    expect(claimControl).not.toHaveBeenCalled();
    expect(requestPointerLock).not.toHaveBeenCalled();
  });

  it("keeps Take control enabled during local ownership and hands the target to direct control", () => {
    const takeDirectControl = vi.fn(async () => undefined);
    mocks.useOperatorController.mockReturnValue(controller(vi.fn(async () => undefined), {
      authenticated: true,
      connection: "live",
      status: armedGatewayStatus(),
      takeDirectControl,
    }));

    render(<App />);
    const target = screen.getByLabelText("Target display");
    const takeControl = screen.getByRole("button", { name: "Take control" });

    expect(takeControl).toBeEnabled();
    fireEvent.click(takeControl);
    expect(takeDirectControl).toHaveBeenCalledOnce();
    expect(takeDirectControl).toHaveBeenCalledWith(target);
  });
});
