import { describe, expect, it } from "vitest";
import type { GatewayStatus } from "../../shared/gateway-contract";
import { deriveProofModules } from "./proof";

const healthyStatus: GatewayStatus = {
  ok: true,
  serial: {
    ready: true,
    device: "/dev/noob-uart",
    firmware: "1.0",
    last_ack_age_ms: 20,
    reconnects: 0,
    last_error: null,
  },
  video: {
    state: "ready",
    generation: 1,
    active_mode_id: "hd",
    requested: {
      id: "hd",
      label: "HD",
      width: 1280,
      height: 720,
      fps: 30,
      pixel_format: "MJPG",
      max_frame_bytes: 2_097_152,
    },
    negotiated: { width: 1280, height: 720, fps: 30, pixel_format: "MJPG" },
    source_timing_detectable: false,
    ready: true,
    device: "/dev/noob-video",
    width: 1280,
    height: 720,
    fps: 15,
    last_frame_age_ms: 10,
    sequence: 4,
    restarts: 0,
    viewers: 1,
    last_error: null,
  },
  local_input: {
    enabled: true,
    ready: true,
    armed: false,
    exclusive_grab: false,
    keyboard_ready: true,
    pointer_ready: true,
    last_event_age_ms: null,
    last_error: null,
    disarm_reason: "operator",
    dropped_events: 0,
  },
  control: { active: false, expires_in_ms: 0, release_required: false },
};

const environmentCameraStatus: NonNullable<GatewayStatus["environment_camera"]> = {
  configured: true,
  reachable: true,
  device_id: `cam_${"a".repeat(16)}`,
  stream_enabled: true,
  sensor_enabled: true,
  sensor_initialized: true,
  power_control: false,
  frame_ready: true,
  generation: 1,
  sequence: 1,
  width: 640,
  height: 480,
  last_frame_age_ms: 10,
  viewers: 1,
  storage: {
    state: "mounted",
    mounted: true,
    writable: true,
    total_bytes: 100,
    free_bytes: 50,
    reserve_bytes: 5,
    media_count: 1,
    active_job_id: null,
    limits: {
      max_media_items: 10,
      max_total_bytes: 100,
      max_clip_duration_ms: 30_000,
      max_clip_fps: 5,
      max_clip_frames: 150,
    },
    last_error: null,
  },
  last_error: null,
};

describe("proof rail truth projection", () => {
  it("never infers target acceptance or unreported baud/HID availability", () => {
    const modules = deriveProofModules(healthyStatus, true, new Date("2026-08-27T12:00:00Z"));
    expect(modules.find((module) => module.id === "target")?.state).toBe("—");
    expect(modules.find((module) => module.id === "uart")?.fields[1].value).toBe("—");
    expect(modules.find((module) => module.id === "hid")?.fields.every((field) => field.value === "—")).toBe(true);
  });

  it("uses explicit unknown states before status is available", () => {
    const modules = deriveProofModules(null, false, null);
    expect(modules.every((module) => module.state === "—")).toBe(true);
  });

  it("omits optional camera status when the gateway reports the feature unconfigured", () => {
    const modules = deriveProofModules({
      ...healthyStatus,
      environment_camera: { ...environmentCameraStatus, configured: false },
    }, true, null);

    expect(modules.find((module) => module.id === "environment")).toBeUndefined();
  });

  it("reports environmental stream and microSD proof without claiming physical power control", () => {
    const modules = deriveProofModules({
      ...healthyStatus,
      environment_camera: environmentCameraStatus,
    }, true, null);

    expect(modules.find((module) => module.id === "environment")).toMatchObject({
      state: "Live",
      fields: [
        { label: "Camera", value: "Stream on" },
        { label: "Storage", value: "Mounted" },
      ],
    });
    expect(modules.find((module) => module.id === "target")?.fields[0].value).toBe("—");
  });
});
