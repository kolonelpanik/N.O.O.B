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

  bootstrapToken(token: string): Promise<GatewayStatus> {
    return guarded(() => bridge().bootstrapToken(token));
  },

  clearToken(): Promise<void> {
    return guarded(() => bridge().clearToken());
  },

  status(): Promise<GatewayStatus> {
    return guarded(() => bridge().getStatus());
  },

  frame() {
    return guarded(() => bridge().getFrame());
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

  onControlLost(listener: (reason: string) => void): () => void {
    return window.noob?.onControlLost(listener) ?? (() => undefined);
  },
};
