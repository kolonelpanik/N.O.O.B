import type {
  GatewayConfigView,
  GatewayInputCommand,
  GatewayStatus,
  PublicGatewayError,
} from "../../shared/gateway-contract";

const FALLBACK_CONFIG: GatewayConfigView = {
  gatewayUrl: "http://127.0.0.1:18765",
  gatewayLabel: "uConsole · 192.0.2.83",
  streamUrl: "",
  tokenConfigured: false,
  connectionMode: "fixed",
  currentDeviceId: null,
};

export class OperatorApiError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(error: PublicGatewayError) {
    super(error.code);
    this.name = "OperatorApiError";
    this.code = error.code;
    this.status = error.status;
  }
}

function bridge() {
  if (window.noob === undefined) {
    throw new OperatorApiError({ code: "desktop_bridge_unavailable", status: null });
  }
  return window.noob;
}

function parsePublicError(error: unknown): OperatorApiError {
  if (error instanceof OperatorApiError) {
    return error;
  }
  const message = error instanceof Error ? error.message : String(error);
  const match = message.match(/\{"code":"([a-z0-9_]+)","status":(null|[0-9]+)\}/i);
  if (match) {
    return new OperatorApiError({
      code: match[1],
      status: match[2] === "null" ? null : Number.parseInt(match[2], 10),
    });
  }
  return new OperatorApiError({ code: "operator_request_failed", status: null });
}

async function guarded<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    throw parsePublicError(error);
  }
}

export const noobApi = {
  bridgeAvailable: window.noob !== undefined,

  async getConfig(): Promise<GatewayConfigView> {
    if (window.noob === undefined) {
      return FALLBACK_CONFIG;
    }
    return guarded(() => bridge().getConfig());
  },

  listDevices() {
    return guarded(() => bridge().listDevices());
  },

  discoverDevices(timeoutMs = 2_000) {
    return guarded(() => bridge().discoverDevices(timeoutMs));
  },

  probeDevice(address: string, sshPort = 22) {
    return guarded(() => bridge().probeDevice(address, sshPort));
  },

  inspectDevice(candidateId: string) {
    return guarded(() => bridge().inspectDevice(candidateId));
  },

  pairAndConnectDevice(candidateId: string, expectedFingerprint: string, profileName: string) {
    return guarded(() => bridge().pairAndConnectDevice(candidateId, expectedFingerprint, profileName));
  },

  connectKnownDevice(deviceId: string) {
    return guarded(() => bridge().connectKnownDevice(deviceId));
  },

  bootstrapToken(token: string): Promise<GatewayStatus> {
    return guarded(() => bridge().bootstrapToken(token));
  },

  clearToken(): Promise<void> {
    return guarded(() => bridge().clearToken());
  },

  status(): Promise<GatewayStatus> {
    return guarded(() => bridge().getStatus());
  },

  frame(source: "target" | "environment" = "target") {
    return guarded(() => bridge().getFrame(source));
  },

  videoModes() {
    return guarded(() => bridge().getVideoModes());
  },

  setVideoMode(modeId: string, expectedGeneration: number) {
    return guarded(() => bridge().setVideoMode(modeId, expectedGeneration));
  },

  claim() {
    return guarded(() => bridge().claimControl());
  },

  renew() {
    return guarded(() => bridge().renewControl());
  },

  release() {
    return guarded(() => bridge().releaseControl());
  },

  send(command: GatewayInputCommand) {
    return guarded(() => bridge().sendInput(command));
  },

  releaseAll() {
    return guarded(() => bridge().releaseAll());
  },

  armLocalInput() {
    return guarded(() => bridge().armLocalInput());
  },

  disarmLocalInput() {
    return guarded(() => bridge().disarmLocalInput());
  },

  setEnvironmentCamera(enabled: boolean, expectedGeneration: number) {
    return guarded(() => bridge().setEnvironmentCamera(enabled, expectedGeneration));
  },

  captureEnvironmentSnapshot(expectedGeneration: number) {
    return guarded(() => bridge().captureEnvironmentSnapshot(expectedGeneration));
  },

  listEnvironmentMedia(limit = 20, cursor?: string) {
    return guarded(() => bridge().listEnvironmentMedia(limit, cursor));
  },

  startEnvironmentClip(durationSeconds: number, fps: number, expectedGeneration: number) {
    return guarded(() => bridge().startEnvironmentClip(durationSeconds, fps, expectedGeneration));
  },

  getEnvironmentClipJob(jobId: string) {
    return guarded(() => bridge().getEnvironmentClipJob(jobId));
  },

  stopEnvironmentClip(jobId: string) {
    return guarded(() => bridge().stopEnvironmentClip(jobId));
  },

  onControlLost(listener: (reason: string) => void): () => void {
    return window.noob?.onControlLost(listener) ?? (() => undefined);
  },
};
