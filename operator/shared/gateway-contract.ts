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

export interface GatewayStatus {
  ok: true;
  serial: SerialStatus;
  video: VideoStatus;
  local_input: LocalInputStatus;
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
  bootstrapToken(token: string): Promise<GatewayStatus>;
  clearToken(): Promise<void>;
  getStatus(): Promise<GatewayStatus>;
  getFrame(): Promise<FrameResult>;
  getVideoModes(): Promise<GatewayVideoModesResult>;
  setVideoMode(modeId: string, expectedGeneration: number): Promise<GatewayVideoModeChangeResult>;
  claimControl(): Promise<GatewayClaimResult>;
  renewControl(): Promise<GatewayClaimResult>;
  releaseControl(): Promise<GatewayOperationResult>;
  sendInput(command: GatewayInputCommand): Promise<GatewayOperationResult>;
  releaseAll(): Promise<GatewayOperationResult>;
  armLocalInput(): Promise<GatewayLocalInputResult>;
  disarmLocalInput(): Promise<GatewayLocalInputResult>;
  onControlLost(listener: (reason: string) => void): () => void;
}

export interface PublicGatewayError {
  code: string;
  status: number | null;
}
