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
});
