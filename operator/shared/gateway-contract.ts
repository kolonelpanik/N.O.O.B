export type KeyEventKind = "down" | "up";
export type MouseButton = "left" | "middle" | "right";

export type GatewayInputCommand =
  | { op: "key"; event: KeyEventKind; key: string }
  | { op: "type"; text: string; interval_ms: number }
  | { op: "combo"; keys: string[]; hold_ms: number }
  | { op: "mouse_move"; dx: number; dy: number; wheel: number }
  | { op: "mouse_button"; button: MouseButton; event: "down" | "up" | "click" }
  | { op: "release_all" }
  | { op: "ping" };

export interface SerialStatus {
  ready: boolean;
  device: string | null;
  firmware: string | null;
  last_ack_age_ms: number | null;
  reconnects: number;
  last_error: string | null;
}

export interface VideoStatus {
  state: VideoCaptureState;
  generation: number;
  active_mode_id: string | null;
  requested: VideoRequestedProfile | null;
  negotiated: VideoSignal | null;
  source_timing_detectable: boolean;
  ready: boolean;
  device: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  last_frame_age_ms: number | null;
  sequence: number | null;
  restarts: number;
  viewers: number;
  last_error: string | null;
}

export type VideoCaptureState =
  | "starting"
  | "ready"
  | "switching"
  | "rolling_back"
  | "reconnecting"
  | "rolled_back"
  | "degraded"
  | "stopped";

export interface VideoSignal {
  width: number;
  height: number;
  fps: number;
  pixel_format: string;
}

export interface VideoRequestedProfile extends VideoSignal {
  id: string;
  label: string;
  max_frame_bytes: number;
}

export interface VideoMode {
  id: string;
  label: string;
  width: number;
  height: number;
  fps: number;
  pixel_format: string;
  max_frame_bytes: number;
  validated: boolean;
}

export interface GatewayVideoModesResult {
  ok: true;
  generation: number;
  active_mode_id: string | null;
  requested: VideoRequestedProfile | null;
  negotiated: VideoSignal | null;
  state: VideoCaptureState;
  modes: VideoMode[];
}

export interface GatewayVideoModeChangeResult {
  ok: true;
  video: VideoStatus;
}

export interface LocalInputStatus {
  enabled: boolean;
  ready: boolean;
  armed: boolean;
  exclusive_grab: boolean;
  keyboard_ready: boolean;
  pointer_ready: boolean;
  last_event_age_ms: number | null;
  last_error: string | null;
  disarm_reason: string | null;
  dropped_events: number;
}

export type EnvironmentCameraJobState =
  | "queued"
  | "running"
  | "cancelling"
  | "complete"
  | "failed"
  | "cancelled";

export interface EnvironmentCameraStorageStatus {
  state: "unconfigured" | "mounting" | "mounted" | "absent" | "read_only" | "full" | "error";
  mounted: boolean;
  writable: boolean;
  free_bytes: number | null;
  total_bytes: number | null;
  reserve_bytes: number;
  media_count: number;
  active_job_id: string | null;
  limits: {
    max_media_items: number;
    max_total_bytes: number;
    max_clip_duration_ms: number;
    max_clip_fps: number;
    max_clip_frames: number;
  };
  last_error: string | null;
}

export interface EnvironmentCameraStatus {
  configured: boolean;
  reachable: boolean;
  device_id: string | null;
  stream_enabled: boolean;
  sensor_enabled: boolean;
  sensor_initialized: boolean;
  power_control: false;
  frame_ready: boolean;
  generation: number;
  sequence: number | null;
  width: number | null;
  height: number | null;
  last_frame_age_ms: number | null;
  viewers: number;
  storage: EnvironmentCameraStorageStatus;
  last_error: string | null;
}

export interface EnvironmentCameraMediaItem {
  id: string;
  kind: "snapshot" | "clip";
  state: "complete";
  created_at: string | null;
  created_uptime_ms: number;
  size_bytes: number;
  width: number;
  height: number;
  frame_count: number;
  fps: number | null;
  duration_ms: number;
  content_type: string;
}

export interface EnvironmentCameraMediaPage {
  ok: true;
  storage: EnvironmentCameraStorageStatus;
  items: EnvironmentCameraMediaItem[];
  next_cursor: string | null;
}

export interface EnvironmentCameraJob {
  job_id: string;
  kind: "clip";
  state: EnvironmentCameraJobState;
  created_uptime_ms: number;
  frames_written: number;
  frames_target: number;
  media_id: string | null;
  error_code: string | null;
}

export interface GatewayStatus {
  ok: true;
  serial: SerialStatus;
  video: VideoStatus;
  local_input: LocalInputStatus;
  environment_camera?: EnvironmentCameraStatus;
  control: {
    active: boolean;
    expires_in_ms: number;
    release_required: boolean;
  };
}

export interface GatewayConfigView {
  gatewayUrl: string;
  gatewayLabel: string;
  streamUrl: string;
  tokenConfigured: boolean;
  connectionMode: "fixed" | "ssh-tunnel";
  currentDeviceId: string | null;
}

export type GatewayDeviceSource = "discovery" | "manual";

export interface GatewayDeviceCandidate {
  candidateId: string;
  instanceName: string;
  address: string;
  sshPort: number;
  hostKeyFingerprint: string | null;
  pairingCode: string | null;
  product: string | null;
  version: string | null;
  capabilities: string[];
  expiresAt: string;
  source: GatewayDeviceSource;
}

export interface GatewayDeviceProfile {
  deviceId: string;
  profileName: string;
  address: string;
  sshPort: number;
  hostKeyFingerprint: string;
  capabilities: string[];
  createdAt: string;
}

export interface GatewayDeviceListResult {
  devices: GatewayDeviceProfile[];
  currentDeviceId: string | null;
}

export interface GatewayDeviceDiscoveryResult {
  candidates: GatewayDeviceCandidate[];
}

export interface GatewayDeviceConnectionResult {
  config: GatewayConfigView;
  device: GatewayDeviceProfile;
}

export interface GatewayClaimResult {
  ok: true;
  ttlMs: number;
}

export interface GatewayOperationResult {
  ok: true;
  released?: boolean;
  result?: unknown;
}

export interface EnvironmentCameraStateResult {
  ok: true;
  environment_camera: EnvironmentCameraStatus;
}

export interface EnvironmentCameraSnapshotResult {
  ok: true;
  item: EnvironmentCameraMediaItem;
}

export interface EnvironmentCameraClipResult {
  ok: true;
  job_id: string;
  state: "queued";
}

export interface EnvironmentCameraJobResult {
  ok: true;
  job: EnvironmentCameraJob;
}

export interface EnvironmentCameraStopResult {
  ok: true;
  job_id: string;
  state: "cancelling" | "cancelled";
}

export interface GatewayLocalInputResult {
  ok: true;
  local_input: LocalInputStatus;
}

export interface FrameResult {
  bytes: Uint8Array;
  contentType: "image/jpeg";
  sequence: string | null;
}

export interface NoobBridge {
  getConfig(): Promise<GatewayConfigView>;
  listDevices(): Promise<GatewayDeviceListResult>;
  discoverDevices(timeoutMs: number): Promise<GatewayDeviceDiscoveryResult>;
  probeDevice(address: string, sshPort: number): Promise<GatewayDeviceCandidate>;
  inspectDevice(candidateId: string): Promise<GatewayDeviceCandidate>;
  pairAndConnectDevice(candidateId: string, expectedFingerprint: string, profileName: string): Promise<GatewayDeviceConnectionResult>;
  connectKnownDevice(deviceId: string): Promise<GatewayDeviceConnectionResult>;
  bootstrapToken(token: string): Promise<GatewayStatus>;
  clearToken(): Promise<void>;
  getStatus(): Promise<GatewayStatus>;
  getFrame(source?: "target" | "environment"): Promise<FrameResult>;
  getVideoModes(): Promise<GatewayVideoModesResult>;
  setVideoMode(modeId: string, expectedGeneration: number): Promise<GatewayVideoModeChangeResult>;
  claimControl(): Promise<GatewayClaimResult>;
  renewControl(): Promise<GatewayClaimResult>;
  releaseControl(): Promise<GatewayOperationResult>;
  sendInput(command: GatewayInputCommand): Promise<GatewayOperationResult>;
  releaseAll(): Promise<GatewayOperationResult>;
  armLocalInput(): Promise<GatewayLocalInputResult>;
  disarmLocalInput(): Promise<GatewayLocalInputResult>;
  setEnvironmentCamera(enabled: boolean, expectedGeneration: number): Promise<EnvironmentCameraStateResult>;
  captureEnvironmentSnapshot(expectedGeneration: number): Promise<EnvironmentCameraSnapshotResult>;
  listEnvironmentMedia(limit: number, cursor?: string): Promise<EnvironmentCameraMediaPage>;
  startEnvironmentClip(durationSeconds: number, fps: number, expectedGeneration: number): Promise<EnvironmentCameraClipResult>;
  getEnvironmentClipJob(jobId: string): Promise<EnvironmentCameraJobResult>;
  stopEnvironmentClip(jobId: string): Promise<EnvironmentCameraStopResult>;
  onControlLost(listener: (reason: string) => void): () => void;
}

export interface PublicGatewayError {
  code: string;
  status: number | null;
}
