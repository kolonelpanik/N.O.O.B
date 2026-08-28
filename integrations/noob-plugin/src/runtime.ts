import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { addKnownHost, loadStore, mutateStore } from "./config.js";
import { candidateExpired, discoverCandidates, probeCandidate, resolveCandidate, stableDeviceId } from "./discovery.js";
import { GatewayError } from "./gateway.js";
import { assertCandidateId, assertControlId, assertDeviceId, assertRequestId } from "./policy.js";
import { TunnelManager } from "./tunnel.js";
import type { Candidate, ControlSession, DeviceProfile, FrameIdentity, InputReceipt, SourceId } from "./types.js";

const FRAME_TOKEN_MAX_AGE_MS = 5_000;
const MAX_DEDUPE_RECEIPTS = 1_024;
const LEASE_PATTERN = /^[0-9a-f]{32}$/;

interface LiveControl extends ControlSession {
  renew_timer: NodeJS.Timeout;
  expiry_timer?: NodeJS.Timeout;
  in_flight_actions: number;
}

function opaque(prefix: string, bytes = 18): string {
  return `${prefix}_${randomBytes(bytes).toString("base64url")}`;
}

function gatewayHeaders(lease: string): HeadersInit {
  return { "X-NOOB-Lease": lease };
}

function normalizeKeys(keys: string[]): string[] {
  const aliases: Record<string, string> = {
    GUI: "LEFT_GUI", CMD: "LEFT_GUI", COMMAND: "LEFT_GUI",
    CTRL: "LEFT_CONTROL", CONTROL: "LEFT_CONTROL",
    SHIFT: "LEFT_SHIFT", ALT: "LEFT_ALT", OPTION: "LEFT_ALT",
  };
  const normalized = keys.map((key) => aliases[key] ?? key);
  if (new Set(normalized).size !== normalized.length) throw new Error("duplicate_combo_key");
  return normalized;
}

export class NoobRuntime {
  readonly tunnels = new TunnelManager();
  private readonly candidates = new Map<string, Candidate>();
  private readonly frameSecret = randomBytes(32);
  private readonly controls = new Map<string, LiveControl>();
  private readonly receipts = new Map<string, InputReceipt>();

  async listDevices(): Promise<object> {
    const store = await loadStore();
    const views = await this.tunnels.listViews();
    return {
      default_device_id: store.default_device_id,
      devices: store.devices.map((profile) => ({
        device_id: profile.device_id,
        profile_name: profile.profile_name,
        address: profile.address,
        ssh_port: profile.ssh_port,
        trust_state: "pinned",
        connection_state: views.get(profile.device_id)?.connection_state ?? "disconnected",
        capabilities: profile.capabilities,
      })),
    };
  }

  async discover(timeoutMs: number): Promise<object> {
    const found = await discoverCandidates(timeoutMs);
    for (const candidate of found) this.candidates.set(candidate.candidate_id, candidate);
    return { candidates: found.map(({ host_key_line: _hidden, ...candidate }) => candidate) };
  }

  async probe(address: string, sshPort: number, timeoutMs: number): Promise<object> {
    const candidate = await probeCandidate(address, sshPort, timeoutMs);
    this.candidates.set(candidate.candidate_id, candidate);
    const { host_key_line: _hidden, ...publicCandidate } = candidate;
    return publicCandidate;
  }

  async register(candidateId: string, expectedFingerprint: string, profileName: string, setDefault: boolean): Promise<object> {
    assertCandidateId(candidateId);
    const current = this.candidates.get(candidateId);
    if (!current || candidateExpired(current)) throw new Error("candidate_expired_or_unknown");
    const candidate = await resolveCandidate(current, 3_000);
    this.candidates.set(candidateId, candidate);
    if (!candidate.observed_host_key_sha256 || !candidate.host_key_line) throw new Error("candidate_host_key_unavailable");
    if (candidate.observed_host_key_sha256 !== expectedFingerprint) throw new Error("host_key_fingerprint_mismatch");
    const deviceId = stableDeviceId(expectedFingerprint);
    const verifiedHostKeyLine = candidate.host_key_line;
    let created = false;
    await mutateStore(async (store) => {
      const endpointConflict = store.devices.find((profile) =>
        profile.device_id !== deviceId &&
        profile.address.toLowerCase() === candidate.address.toLowerCase() &&
        profile.ssh_port === candidate.ssh_port
      );
      if (endpointConflict) throw new Error("device_identity_conflict");
      const existingIndex = store.devices.findIndex((profile) => profile.device_id === deviceId);
      const existing = existingIndex >= 0 ? store.devices[existingIndex] : undefined;
      if (existing && existing.host_key_sha256 !== expectedFingerprint) {
        throw new Error("device_identity_conflict");
      }
      const profile: DeviceProfile = {
        device_id: deviceId,
        profile_name: profileName,
        address: candidate.address,
        ssh_port: candidate.ssh_port,
        ssh_user: existing?.ssh_user ?? (process.env.NOOB_SSH_USER?.trim() || "kali"),
        host_key_sha256: expectedFingerprint,
        host_key_line: verifiedHostKeyLine,
        gateway_port: existing?.gateway_port ?? Number.parseInt(process.env.NOOB_REMOTE_GATEWAY_PORT ?? "8765", 10),
        capabilities: candidate.capabilities.length > 0
          ? [...candidate.capabilities]
          : [...(existing?.capabilities ?? [])],
        created_at: existing?.created_at ?? new Date().toISOString(),
      };
      await addKnownHost(profile);
      if (existingIndex < 0) {
        store.devices.push(profile);
        created = true;
      } else {
        store.devices[existingIndex] = profile;
      }
      if (setDefault || store.default_device_id === null) store.default_device_id = deviceId;
      return store;
    });
    return { device_id: deviceId, created, updated: !created, trust_state: "pinned" };
  }

  async connect(deviceId: string): Promise<object> {
    const view = await this.tunnels.connect(deviceId);
    return {
      device_id: view.device_id,
      connection_state: view.connection_state,
      connection_id: view.connection_id,
      connected_at: view.connected_at,
    };
  }

  async status(deviceId: string): Promise<unknown> {
    return await this.tunnels.api(deviceId).api.json("/api/v1/status");
  }

  private signFrame(identity: FrameIdentity): string {
    const payload = Buffer.from(JSON.stringify(identity)).toString("base64url");
    const signature = createHmac("sha256", this.frameSecret).update(payload).digest("base64url");
    return `ft1.${payload}.${signature}`;
  }

  private verifyFrame(token: string, deviceId: string, source: SourceId = "target"): FrameIdentity {
    const parts = token.split(".");
    if (parts.length !== 3 || parts[0] !== "ft1") throw new Error("invalid_frame_token");
    const payload = parts[1] ?? "";
    const signature = parts[2] ?? "";
    const expected = createHmac("sha256", this.frameSecret).update(payload).digest();
    let provided: Buffer;
    try { provided = Buffer.from(signature, "base64url"); } catch { throw new Error("invalid_frame_token"); }
    if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) throw new Error("invalid_frame_token");
    let identity: FrameIdentity;
    try { identity = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as FrameIdentity; } catch { throw new Error("invalid_frame_token"); }
    const connection = this.tunnels.api(deviceId).view;
    if (identity.device_id !== deviceId || identity.source_id !== source || identity.connection_id !== connection.connection_id) {
      throw new Error("frame_token_scope_mismatch");
    }
    if (
      identity.freshness_basis !== "gateway_ready_at_response"
      || !Number.isSafeInteger(identity.proof_started_at_ms)
      || Date.now() - identity.proof_started_at_ms > FRAME_TOKEN_MAX_AGE_MS
      || identity.proof_started_at_ms > Date.now() + 1_000
    ) {
      throw new Error("stale_frame_token");
    }
    return identity;
  }

  async frame(deviceId: string, source: SourceId): Promise<{ structured: object; bytes: Uint8Array }> {
    assertDeviceId(deviceId);
    const { api, view } = this.tunnels.api(deviceId);
    const frame = await api.frame(source);
    const identity: FrameIdentity = {
      device_id: deviceId,
      source_id: source,
      generation: frame.generation,
      sequence: frame.sequence,
      observed_at: frame.observedAt,
      proof_started_at_ms: frame.proofStartedAtMs,
      freshness_basis: "gateway_ready_at_response",
      connection_id: view.connection_id,
    };
    return {
      structured: {
        device_id: deviceId,
        source_id: source,
        frame_token: this.signFrame(identity),
        sequence: identity.sequence,
        generation: identity.generation,
        observed_at: identity.observed_at,
        capture_time_known: false,
        freshness_basis: identity.freshness_basis,
        content_type: "image/jpeg",
        stale: false,
      },
      bytes: frame.bytes,
    };
  }

  async setCameraStreaming(deviceId: string, enabled: boolean, expectedGeneration: number): Promise<unknown> {
    const { api } = this.tunnels.api(deviceId);
    return await api.post("/api/v1/environment-camera/state", { enabled, expected_generation: expectedGeneration });
  }

  async listMedia(deviceId: string, cursor: string | undefined, limit: number): Promise<unknown> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set("cursor", cursor);
    return await this.tunnels.api(deviceId).api.json(`/api/v1/environment-camera/storage?${query}`);
  }

  async getMedia(deviceId: string, mediaId: string): Promise<{ metadata: unknown; bytes?: Uint8Array }> {
    const { api } = this.tunnels.api(deviceId);
    if (!/^m_[0-9a-f]{32}$/.test(mediaId)) throw new Error("invalid_media_id");
    const metadata = await api.json<{ item?: { kind?: unknown } }>(`/api/v1/environment-camera/storage/${encodeURIComponent(mediaId)}`);
    if (metadata.item?.kind === "snapshot") {
      const bytes = await api.jpeg(`/api/v1/environment-camera/storage/${encodeURIComponent(mediaId)}/content`);
      return { metadata, bytes };
    }
    return { metadata };
  }

  async getClipFrame(
    deviceId: string,
    mediaId: string,
    frameIndex: number,
  ): Promise<{ metadata: unknown; frameIndex: number; bytes: Uint8Array }> {
    assertDeviceId(deviceId);
    if (!/^m_[0-9a-f]{32}$/.test(mediaId)) throw new Error("invalid_media_id");
    if (!Number.isSafeInteger(frameIndex) || frameIndex < 0 || frameIndex > 149) {
      throw new Error("invalid_clip_frame_index");
    }
    const { api } = this.tunnels.api(deviceId);
    const metadata = await api.json<{
      item?: {
        kind?: unknown;
        state?: unknown;
        frame_count?: unknown;
      };
    }>(`/api/v1/environment-camera/storage/${encodeURIComponent(mediaId)}`);
    const item = metadata.item;
    if (
      item?.kind !== "clip"
      || item.state !== "complete"
      || !Number.isSafeInteger(item.frame_count)
      || (item.frame_count as number) < 1
      || (item.frame_count as number) > 150
    ) {
      throw new Error("media_not_completed_clip");
    }
    if (frameIndex >= (item.frame_count as number)) {
      throw new Error("clip_frame_out_of_range");
    }
    const bytes = await api.jpeg(
      `/api/v1/environment-camera/storage/${encodeURIComponent(mediaId)}/frames/${frameIndex}.jpg`,
    );
    return { metadata, frameIndex, bytes };
  }

  async saveScreenshot(deviceId: string, frameToken: string): Promise<unknown> {
    const identity = this.verifyFrame(frameToken, deviceId, "environment");
    return await this.tunnels.api(deviceId).api.post("/api/v1/environment-camera/snapshot", {
      expected_generation: identity.generation,
    });
  }

  async startRecording(deviceId: string, durationSeconds: number, fps: number, expectedGeneration: number): Promise<unknown> {
    return await this.tunnels.api(deviceId).api.post("/api/v1/environment-camera/clip", {
      duration_seconds: durationSeconds,
      fps,
      expected_generation: expectedGeneration,
    });
  }

  async recordingStatus(deviceId: string, jobId: string): Promise<unknown> {
    if (!/^j_[0-9a-f]{32}$/.test(jobId)) throw new Error("invalid_camera_job_id");
    return await this.tunnels.api(deviceId).api.json(`/api/v1/environment-camera/jobs/${encodeURIComponent(jobId)}`);
  }

  async stopRecording(deviceId: string, jobId: string): Promise<unknown> {
    if (!/^j_[0-9a-f]{32}$/.test(jobId)) throw new Error("invalid_camera_job_id");
    return await this.tunnels.api(deviceId).api.post(`/api/v1/environment-camera/jobs/${encodeURIComponent(jobId)}/stop`, {});
  }

  async acquireControl(deviceId: string, observedFrameToken: string, maxDurationSeconds: number): Promise<object> {
    this.verifyFrame(observedFrameToken, deviceId, "target");
    const { api, view } = this.tunnels.api(deviceId);
    const claim = await api.post<{ lease: string; ttl_ms: number }>("/api/v1/control/claim", {});
    if (!LEASE_PATTERN.test(claim.lease)) throw new Error("invalid_control_claim");
    const controlId = opaque("ctl", 24);
    const expiresAt = Date.now() + maxDurationSeconds * 1_000;
    const session: LiveControl = {
      control_session_id: controlId,
      device_id: deviceId,
      connection_id: view.connection_id,
      lease: claim.lease,
      expires_at_ms: expiresAt,
      idle_timeout_ms: Math.min(5_000, claim.ttl_ms),
      last_used_at_ms: Date.now(),
      in_flight_actions: 0,
      renew_timer: setInterval(() => void this.renewControl(controlId), Math.max(750, Math.min(2_000, Math.floor(claim.ttl_ms / 2)))),
    };
    session.renew_timer.unref();
    this.controls.set(controlId, session);
    this.armControlExpiry(session);
    return { control_session_id: controlId, expires_at: new Date(expiresAt).toISOString(), idle_timeout_ms: session.idle_timeout_ms };
  }

  private controlExpired(session: LiveControl, now = Date.now()): boolean {
    if (now >= session.expires_at_ms) return true;
    return session.in_flight_actions === 0
      && now >= session.last_used_at_ms + session.idle_timeout_ms;
  }

  private armControlExpiry(session: LiveControl): void {
    if (session.expiry_timer) clearTimeout(session.expiry_timer);
    const idleDeadline = session.in_flight_actions > 0
      ? Number.POSITIVE_INFINITY
      : session.last_used_at_ms + session.idle_timeout_ms;
    const deadline = Math.min(session.expires_at_ms, idleDeadline);
    session.expiry_timer = setTimeout(
      () => void this.expireControl(session.control_session_id),
      Math.max(0, deadline - Date.now()),
    );
    session.expiry_timer.unref();
  }

  private beginControlAction(session: LiveControl): void {
    session.in_flight_actions += 1;
    this.armControlExpiry(session);
  }

  private finishControlAction(session: LiveControl, completed: boolean): void {
    session.in_flight_actions = Math.max(0, session.in_flight_actions - 1);
    if (completed) session.last_used_at_ms = Date.now();
    if (this.controls.get(session.control_session_id) === session) this.armControlExpiry(session);
  }

  private async expireControl(controlId: string): Promise<void> {
    const session = this.controls.get(controlId);
    if (!session) return;
    if (!this.controlExpired(session)) {
      this.armControlExpiry(session);
      return;
    }
    this.forgetControl(controlId);
    try {
      await this.tunnels.api(session.device_id).api.post(
        "/api/v1/control/release",
        {},
        gatewayHeaders(session.lease),
      );
    } catch {
      // The local authority is already closed. The gateway lease remains
      // bounded by its own TTL if the best-effort release cannot be delivered.
    }
  }

  private async renewControl(controlId: string): Promise<void> {
    const session = this.controls.get(controlId);
    if (!session) return;
    if (this.controlExpired(session)) return await this.expireControl(controlId);
    try {
      await this.tunnels.api(session.device_id).api.post("/api/v1/control/renew", {}, gatewayHeaders(session.lease));
    } catch {
      this.forgetControl(controlId);
    }
  }

  private control(controlId: string): LiveControl {
    assertControlId(controlId);
    const session = this.controls.get(controlId);
    if (!session) throw new Error("control_session_unknown");
    const connection = this.tunnels.api(session.device_id).view;
    if (session.connection_id !== connection.connection_id) {
      this.forgetControl(controlId);
      throw new Error("control_session_expired");
    }
    if (this.controlExpired(session)) {
      void this.expireControl(controlId);
      throw new Error("control_session_expired");
    }
    return session;
  }

  private receipt(requestId: string): InputReceipt | null {
    assertRequestId(requestId);
    return this.receipts.get(requestId) ?? null;
  }

  private storeReceipt(receipt: InputReceipt): InputReceipt {
    this.receipts.set(receipt.request_id, receipt);
    while (this.receipts.size > MAX_DEDUPE_RECEIPTS) {
      const first = this.receipts.keys().next().value as string | undefined;
      if (!first) break;
      this.receipts.delete(first);
    }
    return receipt;
  }

  async input(controlId: string, observedFrameToken: string, requestId: string, command: object): Promise<InputReceipt> {
    const duplicate = this.receipt(requestId);
    if (duplicate) return duplicate;
    const session = this.control(controlId);
    this.verifyFrame(observedFrameToken, session.device_id, "target");
    this.beginControlAction(session);
    let completed = false;
    try {
      const result = await this.tunnels.api(session.device_id).api.post<{ ok?: boolean }>("/api/v1/input", command, gatewayHeaders(session.lease));
      completed = true;
      return this.storeReceipt({
        request_id: requestId,
        accepted: true,
        transport_acknowledged: result.ok === true,
        target_acceptance: "unverified",
        verification_required: true,
      });
    } catch (error) {
      if (error instanceof GatewayError && (error.message === "gateway_timeout" || error.message === "gateway_unreachable")) {
        await this.emergencyRelease(session.device_id).catch(() => undefined);
        throw new Error("transport_uncertain");
      }
      throw error;
    } finally {
      this.finishControlAction(session, completed);
    }
  }

  async typeText(controlId: string, text: string, intervalMs: number, frameToken: string, requestId: string): Promise<InputReceipt> {
    return await this.input(controlId, frameToken, requestId, { op: "type", text, interval_ms: intervalMs });
  }

  async combo(controlId: string, keys: string[], holdMs: number, frameToken: string, requestId: string): Promise<InputReceipt> {
    return await this.input(controlId, frameToken, requestId, { op: "combo", keys: normalizeKeys(keys), hold_ms: holdMs });
  }

  async move(controlId: string, dx: number, dy: number, wheel: number, frameToken: string, requestId: string): Promise<InputReceipt> {
    return await this.input(controlId, frameToken, requestId, { op: "mouse_move", dx, dy, wheel });
  }

  async click(controlId: string, button: string, count: number, intervalMs: number, frameToken: string, requestId: string): Promise<InputReceipt> {
    const duplicate = this.receipt(requestId);
    if (duplicate) return duplicate;
    const session = this.control(controlId);
    this.verifyFrame(frameToken, session.device_id, "target");
    this.beginControlAction(session);
    let acknowledged = true;
    let completed = false;
    try {
      for (let index = 0; index < count; index += 1) {
        const result = await this.tunnels.api(session.device_id).api.post<{ ok?: boolean }>(
          "/api/v1/input",
          { op: "mouse_button", button, event: "click" },
          gatewayHeaders(session.lease),
        );
        acknowledged = acknowledged && result.ok === true;
        if (index + 1 < count) await new Promise((resolve) => setTimeout(resolve, intervalMs));
      }
      completed = true;
    } catch (error) {
      if (error instanceof GatewayError && (error.message === "gateway_timeout" || error.message === "gateway_unreachable")) {
        await this.emergencyRelease(session.device_id).catch(() => undefined);
        throw new Error("transport_uncertain");
      }
      throw error;
    } finally {
      this.finishControlAction(session, completed);
    }
    return this.storeReceipt({
      request_id: requestId,
      accepted: true,
      transport_acknowledged: acknowledged,
      target_acceptance: "unverified",
      verification_required: true,
    });
  }

  async drag(controlId: string, button: string, path: Array<{ dx: number; dy: number; duration_ms: number }>, frameToken: string, requestId: string): Promise<InputReceipt> {
    const duplicate = this.receipt(requestId);
    if (duplicate) return duplicate;
    const session = this.control(controlId);
    this.verifyFrame(frameToken, session.device_id, "target");
    this.beginControlAction(session);
    let acknowledged = true;
    let completed = false;
    try {
      const down = await this.tunnels.api(session.device_id).api.post<{ ok?: boolean }>("/api/v1/input", { op: "mouse_button", button, event: "down" }, gatewayHeaders(session.lease));
      acknowledged = down.ok === true;
      for (const step of path) {
        const moved = await this.tunnels.api(session.device_id).api.post<{ ok?: boolean }>("/api/v1/input", { op: "mouse_move", dx: step.dx, dy: step.dy, wheel: 0 }, gatewayHeaders(session.lease));
        acknowledged = acknowledged && moved.ok === true;
        await new Promise((resolve) => setTimeout(resolve, step.duration_ms));
      }
      completed = true;
    } finally {
      try {
        await this.tunnels.api(session.device_id).api.post("/api/v1/input", { op: "mouse_button", button, event: "up" }, gatewayHeaders(session.lease));
      } catch {
        await this.emergencyRelease(session.device_id).catch(() => undefined);
      }
      this.finishControlAction(session, completed);
    }
    return this.storeReceipt({ request_id: requestId, accepted: true, transport_acknowledged: acknowledged, target_acceptance: "unverified", verification_required: true });
  }

  private forgetControl(controlId: string): void {
    const session = this.controls.get(controlId);
    if (!session) return;
    clearInterval(session.renew_timer);
    if (session.expiry_timer) clearTimeout(session.expiry_timer);
    this.controls.delete(controlId);
  }

  async releaseControl(controlId: string): Promise<object> {
    const session = this.control(controlId);
    this.forgetControl(controlId);
    await this.tunnels.api(session.device_id).api.post("/api/v1/control/release", {}, gatewayHeaders(session.lease));
    return { released: true };
  }

  async emergencyRelease(deviceId: string): Promise<object> {
    assertDeviceId(deviceId);
    const sessions = [...this.controls.values()].filter((session) => session.device_id === deviceId);
    try {
      const result = await this.tunnels.api(deviceId).api.post<{ ok?: boolean }>("/api/v1/release-all", {});
      return { released: true, control_session_closed: sessions.length > 0, serial_acknowledged: result.ok === true };
    } finally {
      for (const session of sessions) this.forgetControl(session.control_session_id);
    }
  }

  async close(): Promise<void> {
    for (const session of this.controls.values()) {
      clearInterval(session.renew_timer);
      if (session.expiry_timer) clearTimeout(session.expiry_timer);
    }
    this.controls.clear();
    await this.tunnels.closeAll();
  }
}
