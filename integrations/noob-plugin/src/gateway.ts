import type { SourceId } from "./types.js";

const DEFAULT_TIMEOUT_MS = 4_000;
const ENVIRONMENT_FRAME_TIMEOUT_MS = 35_000;
const MAX_JSON_BYTES = 1_048_576;
const MAX_JPEG_BYTES = 8_388_608;

export interface GatewayFrame {
  bytes: Uint8Array;
  sequence: number;
  generation: number;
  observedAt: string;
  proofStartedAtMs: number;
}

export class GatewayError extends Error {
  readonly status: number | null;

  constructor(code: string, status: number | null = null) {
    super(code);
    this.name = "GatewayError";
    this.status = status;
  }
}

async function boundedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > maxBytes) throw new GatewayError("gateway_response_too_large", response.status);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > maxBytes) throw new GatewayError("gateway_response_too_large", response.status);
  return bytes;
}

function framePath(source: SourceId): string {
  return source === "target" ? "/api/v1/frame.jpg" : "/api/v1/environment-camera/frame.jpg";
}

function boundedIntegerHeader(response: Response, name: string): number {
  const value = response.headers.get(name);
  if (!value || !/^(?:0|[1-9][0-9]{0,15})$/.test(value)) {
    throw new GatewayError("invalid_frame_proof", response.status);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new GatewayError("invalid_frame_proof", response.status);
  }
  return parsed;
}

export class GatewayApi {
  constructor(
    readonly baseUrl: string,
    private readonly bearer: string,
  ) {}

  async request(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
    if (!path.startsWith("/")) throw new GatewayError("invalid_gateway_path");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${this.bearer}`);
      headers.set("Accept", "application/json, image/jpeg");
      const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers, signal: controller.signal, redirect: "error" });
      if (!response.ok) {
        let code = `gateway_http_${response.status}`;
        try {
          const body = JSON.parse(new TextDecoder().decode(await boundedBody(response, 16_384))) as { error?: unknown };
          if (typeof body.error === "string" && /^[a-z0-9_]{2,80}$/.test(body.error)) code = body.error;
        } catch {
          // Keep the bounded content-free error.
        }
        throw new GatewayError(code, response.status);
      }
      return response;
    } catch (error) {
      if (error instanceof GatewayError) throw error;
      throw new GatewayError(error instanceof DOMException && error.name === "AbortError" ? "gateway_timeout" : "gateway_unreachable");
    } finally {
      clearTimeout(timer);
    }
  }

  async json<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    const response = await this.request(path, init, timeoutMs);
    try {
      return JSON.parse(new TextDecoder().decode(await boundedBody(response, MAX_JSON_BYTES))) as T;
    } catch (error) {
      if (error instanceof GatewayError) throw error;
      throw new GatewayError("invalid_gateway_json", response.status);
    }
  }

  async post<T>(path: string, body: object, headers?: HeadersInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    const allHeaders = new Headers(headers);
    allHeaders.set("Content-Type", "application/json");
    return await this.json<T>(path, { method: "POST", headers: allHeaders, body: JSON.stringify(body) }, timeoutMs);
  }

  async put<T>(path: string, body: object, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    return await this.json<T>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }, timeoutMs);
  }

  async frame(source: SourceId): Promise<GatewayFrame> {
    const proofStartedAtMs = Date.now();
    const timeoutMs = source === "target" ? DEFAULT_TIMEOUT_MS : ENVIRONMENT_FRAME_TIMEOUT_MS;
    const response = await this.request(framePath(source), {}, timeoutMs);
    const type = response.headers.get("content-type")?.split(";", 1)[0];
    if (type !== "image/jpeg") throw new GatewayError("invalid_frame_content_type", response.status);
    const bytes = await boundedBody(response, MAX_JPEG_BYTES);
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes.at(-2) !== 0xff || bytes.at(-1) !== 0xd9) {
      throw new GatewayError("invalid_jpeg_frame", response.status);
    }
    const generationHeader = source === "target"
      ? "x-noob-video-generation"
      : "x-noob-environment-generation";
    return {
      bytes,
      sequence: boundedIntegerHeader(response, "x-noob-frame-sequence"),
      generation: boundedIntegerHeader(response, generationHeader),
      observedAt: new Date().toISOString(),
      proofStartedAtMs,
    };
  }

  async jpeg(path: string): Promise<Uint8Array> {
    const response = await this.request(path);
    const type = response.headers.get("content-type")?.split(";", 1)[0];
    if (type !== "image/jpeg") throw new GatewayError("invalid_media_content_type", response.status);
    const bytes = await boundedBody(response, MAX_JPEG_BYTES);
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8 || bytes.at(-2) !== 0xff || bytes.at(-1) !== 0xd9) {
      throw new GatewayError("invalid_jpeg_media", response.status);
    }
    return bytes;
  }
}
