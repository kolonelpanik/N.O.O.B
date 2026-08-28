import type { GatewayStatus } from "../../shared/gateway-contract";

export type ProofTone = "healthy" | "degraded" | "unknown";

export interface ProofField {
  label: string;
  value: string;
}

export interface ProofModuleModel {
  id: "session" | "video" | "uart" | "hid" | "environment" | "target";
  title: string;
  state: string;
  tone: ProofTone;
  fields: ProofField[];
}

const EM_DASH = "—";

function textOrDash(value: string | null | undefined): string {
  return value?.trim() ? value : EM_DASH;
}

export function formatSessionTime(value: Date | null): string {
  if (value === null) return EM_DASH;
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function deriveProofModules(
  status: GatewayStatus | null,
  connected: boolean,
  sessionStartedAt: Date | null,
): ProofModuleModel[] {
  const serialReady = status?.serial.ready === true;
  const videoReady = status?.video.ready === true;
  const environment = status?.environment_camera;
  const environmentReady = environment?.frame_ready === true;
  return [
    {
      id: "session",
      title: "Session",
      state: connected ? "Active" : EM_DASH,
      tone: connected ? "healthy" : "unknown",
      fields: [
        { label: "ID", value: EM_DASH },
        { label: "Started", value: formatSessionTime(sessionStartedAt) },
      ],
    },
    {
      id: "video",
      title: "Video",
      state: videoReady ? "Live" : status ? "Degraded" : EM_DASH,
      tone: videoReady ? "healthy" : status ? "degraded" : "unknown",
      fields: [
        { label: "Source", value: textOrDash(status?.video.device) },
        {
          label: "Resolution",
          value:
            status?.video.width && status.video.height
              ? `${status.video.width} × ${status.video.height}`
              : EM_DASH,
        },
      ],
    },
    {
      id: "uart",
      title: "UART",
      state: serialReady ? "Live" : status ? "Degraded" : EM_DASH,
      tone: serialReady ? "healthy" : status ? "degraded" : "unknown",
      fields: [
        { label: "Port", value: textOrDash(status?.serial.device) },
        { label: "Baud", value: EM_DASH },
      ],
    },
    {
      id: "hid",
      title: "HID",
      state: serialReady ? "Ready" : status ? "Degraded" : EM_DASH,
      tone: serialReady ? "healthy" : status ? "degraded" : "unknown",
      fields: [
        { label: "Keyboard", value: EM_DASH },
        { label: "Mouse", value: EM_DASH },
      ],
    },
    {
      id: "environment",
      title: "Environment",
      state: environmentReady ? "Live" : environment ? (environment.reachable ? "Idle" : "Degraded") : EM_DASH,
      tone: environmentReady ? "healthy" : environment ? (environment.reachable ? "unknown" : "degraded") : "unknown",
      fields: [
        {
          label: "Camera",
          value: environment === undefined
            ? EM_DASH
            : environment.stream_enabled ? "Stream on" : "Stream off",
        },
        {
          label: "Storage",
          value: environment === undefined
            ? EM_DASH
            : environment.storage.mounted
              ? environment.storage.writable ? "Mounted" : "Read only"
              : textOrDash(environment.storage.state),
        },
      ],
    },
    {
      id: "target",
      title: "Target",
      state: EM_DASH,
      tone: "unknown",
      fields: [
        { label: "Power", value: EM_DASH },
        { label: "State", value: EM_DASH },
      ],
    },
  ];
}
