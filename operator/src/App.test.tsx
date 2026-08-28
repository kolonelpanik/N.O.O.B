import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GatewayConfigView } from "../shared/gateway-contract";
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
  AppShell: ({ onDevices }: { onDevices(): void }) => (
    <main>
      <button type="button" onClick={onDevices}>Devices</button>
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

function controller(adoptConnection: OperatorController["adoptConnection"]): OperatorController {
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
