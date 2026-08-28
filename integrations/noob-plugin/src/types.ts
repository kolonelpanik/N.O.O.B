export type SourceId = "target" | "environment";

export interface DeviceProfile {
  device_id: string;
  profile_name: string;
  address: string;
  ssh_port: number;
  ssh_user: string;
  host_key_sha256: string;
  host_key_line: string;
  gateway_port: number;
  capabilities: string[];
  created_at: string;
}

export interface DeviceStore {
  version: 2;
  default_device_id: string | null;
  devices: DeviceProfile[];
}

export interface Candidate {
  candidate_id: string;
  instance_name: string;
  address: string;
  ssh_port: number;
  observed_host_key_sha256: string | null;
  pairing_code: string | null;
  host_key_line: string | null;
  product: string | null;
  version: string | null;
  capabilities: string[];
  expires_at: string;
}

export interface ConnectionView {
  device_id: string;
  connection_id: string;
  connection_state: "connected" | "degraded";
  connected_at: string;
  local_port: number;
}

export interface GatewayConnection extends ConnectionView {
  base_url: string;
  bearer: string;
  close(): Promise<void>;
}

export interface FrameIdentity {
  device_id: string;
  source_id: SourceId;
  generation: number;
  sequence: number;
  observed_at: string;
  proof_started_at_ms: number;
  freshness_basis: "gateway_ready_at_response";
  connection_id: string;
}

export interface ControlSession {
  control_session_id: string;
  device_id: string;
  connection_id: string;
  lease: string;
  expires_at_ms: number;
  idle_timeout_ms: number;
  last_used_at_ms: number;
}

export interface InputReceipt {
  request_id: string;
  accepted: true;
  transport_acknowledged: boolean;
  target_acceptance: "unverified";
  verification_required: true;
}
