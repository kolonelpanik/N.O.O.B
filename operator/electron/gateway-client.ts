import type {
  FrameResult,
  GatewayClaimResult,
  GatewayInputCommand,
  GatewayLocalInputResult,
  GatewayOperationResult,
  GatewayStatus,
  GatewayVideoModeChangeResult,
  GatewayVideoModesResult,
  PublicGatewayError,
} from "../shared/gateway-contract.js";

const TOKEN_PATTERN = /^[\x21-\x7e]{32,256}$/;
const LEASE_PATTERN = /^[0-9a-f]{32}$/;
const REQUEST_TIMEOUT_MS = 4_000;
const VIDEO_MODE_REQUEST_TIMEOUT_MS = 65_000;

export class GatewayClientError extends Error {
  readonly publicError: PublicGatewayError;

  constructor(code: string, status: number | null = null) {
    super(code);
    this.name = "GatewayClientError";
    this.publicError = { code, status };
  }
}

function normalizedBaseUrl(raw: string): string {
  const url = new URL(raw);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("NOOB_GATEWAY_URL must use http or https");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("NOOB_GATEWAY_URL must not contain credentials, query, or fragment");
  }
  return url.toString().replace(/\/$/, "");
}

async function publicErrorFromResponse(response: Response): Promise<GatewayClientError> {
  let code = `http_${response.status}`;
  try {
    const payload = (await response.json()) as { error?: unknown };
    if (typeof payload.error === "string" && payload.error.length <= 80) {
      code = payload.error;
    }
  } catch {
    // The public error deliberately stays content-free.
  }
  return new GatewayClientError(code, response.status);
}

export class GatewayClient {
  readonly baseUrl: string;
  private token: string | null = null;
  private lease: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = normalizedBaseUrl(baseUrl);
  }

  get tokenConfigured(): boolean {
    return this.token !== null;
  }

  get hasLease(): boolean {
    return this.lease !== null;
  }

  setToken(token: string): void {
    if (!TOKEN_PATTERN.test(token)) {
      throw new GatewayClientError("invalid_token");
    }
    this.token = token;
    this.lease = null;
  }

  clearToken(): void {
    this.token = null;
    this.lease = null;
  }

  clearLease(): void {
    this.lease = null;
  }

  async status(): Promise<GatewayStatus> {
    return this.json<GatewayStatus>("/api/v1/status", { method: "GET" });
  }

  async frame(): Promise<FrameResult> {
    const response = await this.request("/api/v1/frame.jpg", { method: "GET" });
    const contentType = response.headers.get("content-type")?.split(";", 1)[0];
    if (contentType !== "image/jpeg") {
      throw new GatewayClientError("invalid_frame", response.status);
    }
    return {
      bytes: new Uint8Array(await response.arrayBuffer()),
      contentType: "image/jpeg",
      sequence: response.headers.get("x-noob-frame-sequence"),
    };
  }

  async videoModes(): Promise<GatewayVideoModesResult> {
    return this.json<GatewayVideoModesResult>("/api/v1/video/modes", { method: "GET" });
  }

  async setVideoMode(
    modeId: string,
    expectedGeneration: number,
  ): Promise<GatewayVideoModeChangeResult> {
    if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(modeId) || !Number.isSafeInteger(expectedGeneration) || expectedGeneration < 0) {
      throw new GatewayClientError("invalid_video_mode_request");
    }
    return this.json<GatewayVideoModeChangeResult>(
      "/api/v1/video/mode",
      this.jsonRequest({ mode_id: modeId, expected_generation: expectedGeneration }),
      VIDEO_MODE_REQUEST_TIMEOUT_MS,
      "video_mode_unconfirmed",
    );
  }

  async stream(signal?: AbortSignal): Promise<Response> {
    return this.request("/api/v1/stream.mjpeg", { method: "GET", signal }, null);
  }

  async claim(): Promise<GatewayClaimResult> {
    const payload = await this.json<{ ok: true; lease: string; ttl_ms: number }>(
      "/api/v1/control/claim",
      this.jsonRequest(),
    );
    if (!LEASE_PATTERN.test(payload.lease) || !Number.isFinite(payload.ttl_ms)) {
      throw new GatewayClientError("invalid_claim_response");
    }
    this.lease = payload.lease;
    return { ok: true, ttlMs: payload.ttl_ms };
  }

  async renew(): Promise<GatewayClaimResult> {
    const payload = await this.leaseJson<{ ok: true; ttl_ms: number }>(
      "/api/v1/control/renew",
      this.jsonRequest(),
    );
    return { ok: true, ttlMs: payload.ttl_ms };
  }

  async release(): Promise<GatewayOperationResult> {
    try {
      return await this.leaseJson<GatewayOperationResult>(
        "/api/v1/control/release",
        this.jsonRequest(),
      );
    } finally {
      this.lease = null;
    }
  }

  async input(command: GatewayInputCommand): Promise<GatewayOperationResult> {
    return this.leaseJson<GatewayOperationResult>(
      "/api/v1/input",
      this.jsonRequest(command),
    );
  }

  async releaseAll(): Promise<GatewayOperationResult> {
    try {
      return await this.json<GatewayOperationResult>(
        "/api/v1/release-all",
        this.jsonRequest(),
      );
    } finally {
      this.lease = null;
    }
  }

  async armLocalInput(): Promise<GatewayLocalInputResult> {
    return this.json<GatewayLocalInputResult>(
      "/api/v1/local-input/arm",
      this.jsonRequest(),
    );
  }

  async disarmLocalInput(): Promise<GatewayLocalInputResult> {
    return this.json<GatewayLocalInputResult>(
      "/api/v1/local-input/disarm",
      this.jsonRequest(),
    );
  }

  private jsonRequest(body: object = {}): RequestInit {
    return {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
  }

  private async leaseJson<T>(path: string, init: RequestInit): Promise<T> {
    if (this.lease === null) {
      throw new GatewayClientError("lease_required", 409);
    }
    const headers = new Headers(init.headers);
    headers.set("X-NOOB-Lease", this.lease);
    try {
      return await this.json<T>(path, { ...init, headers });
    } catch (error) {
      if (
        error instanceof GatewayClientError &&
        (error.publicError.code === "lease_invalid" || error.publicError.code === "lease_required")
      ) {
        this.lease = null;
      }
      throw error;
    }
  }

  private async json<T>(
    path: string,
    init: RequestInit,
    timeoutMs = REQUEST_TIMEOUT_MS,
    timeoutErrorCode = "gateway_unreachable",
  ): Promise<T> {
    const response = await this.request(path, init, timeoutMs, timeoutErrorCode);
    try {
      return (await response.json()) as T;
    } catch {
      throw new GatewayClientError("invalid_json", response.status);
    }
  }

  private async request(
    path: string,
    init: RequestInit,
    timeoutMs: number | null = REQUEST_TIMEOUT_MS,
    timeoutErrorCode = "gateway_unreachable",
  ): Promise<Response> {
    if (this.token === null) {
      throw new GatewayClientError("token_required", 401);
    }
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.token}`);
    headers.set(
      "Accept",
      path.endsWith(".jpg")
        ? "image/jpeg"
        : path.endsWith(".mjpeg")
          ? "multipart/x-mixed-replace"
          : "application/json",
    );

    const timeoutSignal = init.signal === undefined && timeoutMs !== null
      ? AbortSignal.timeout(timeoutMs)
      : undefined;
    const signal = init.signal ?? timeoutSignal;
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal,
        cache: "no-store",
      });
    } catch {
      if (timeoutSignal?.aborted === true) {
        throw new GatewayClientError(timeoutErrorCode);
      }
      throw new GatewayClientError("gateway_unreachable");
    }
    if (!response.ok) {
      throw await publicErrorFromResponse(response);
    }
    return response;
  }
}
