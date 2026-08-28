import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  EnvironmentCameraMediaItem,
  EnvironmentCameraStatus,
} from "../../shared/gateway-contract";
import type { EnvironmentCameraController } from "../hooks/useEnvironmentCamera";
import { EnvironmentCameraPanel } from "./EnvironmentCameraPanel";

const media: EnvironmentCameraMediaItem = {
  id: `m_${"a".repeat(32)}`,
  kind: "snapshot",
  state: "complete",
  created_at: "2026-08-27T19:00:00Z",
  created_uptime_ms: 1,
  size_bytes: 2048,
  width: 640,
  height: 480,
  frame_count: 1,
  fps: null,
  duration_ms: 0,
  content_type: "image/jpeg",
};

const clip: EnvironmentCameraMediaItem = {
  id: `m_${"c".repeat(32)}`,
  kind: "clip",
  state: "complete",
  created_at: "2026-08-27T19:01:00Z",
  created_uptime_ms: 2,
  size_bytes: 4096,
  width: 640,
  height: 480,
  frame_count: 3,
  fps: 1,
  duration_ms: 3000,
  content_type: "application/vnd.noob.clip+json",
};

const camera: EnvironmentCameraStatus = {
  configured: true,
  reachable: true,
  device_id: `cam_${"b".repeat(16)}`,
  stream_enabled: true,
  sensor_enabled: true,
  sensor_initialized: true,
  power_control: false,
  frame_ready: true,
  generation: 9,
  sequence: 2,
  width: 640,
  height: 480,
  last_frame_age_ms: 8,
  viewers: 1,
  storage: {
    state: "mounted",
    mounted: true,
    writable: true,
    total_bytes: 32 * 1024 * 1024,
    free_bytes: 24 * 1024 * 1024,
    reserve_bytes: 1024,
    media_count: 1,
    active_job_id: null,
    limits: {
      max_media_items: 100,
      max_total_bytes: 32 * 1024 * 1024,
      max_clip_duration_ms: 30_000,
      max_clip_fps: 5,
      max_clip_frames: 150,
    },
    last_error: null,
  },
  last_error: null,
};

function controller(overrides: Partial<EnvironmentCameraController> = {}): EnvironmentCameraController {
  return {
    camera,
    storage: camera.storage,
    media: [media],
    nextCursor: null,
    activeJob: null,
    activeJobId: null,
    busy: false,
    mediaBusy: false,
    error: null,
    setStreaming: vi.fn(async () => true),
    captureSnapshot: vi.fn(async () => media),
    startClip: vi.fn(async () => true),
    stopClip: vi.fn(async () => true),
    refreshMedia: vi.fn(async () => undefined),
    loadMore: vi.fn(async () => undefined),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EnvironmentCameraPanel", () => {
  it("truthfully separates logical camera streaming from unavailable physical power control", () => {
    const model = controller();
    render(<EnvironmentCameraPanel controller={model} />);

    expect(screen.getByText(/physical USB power is not switchable and remains on/i)).toBeInTheDocument();
    expect(screen.getByText("No automatic recording")).toBeInTheDocument();
    const streamSwitch = screen.getByRole("switch", { name: "On" });
    expect(streamSwitch).toHaveAttribute("aria-checked", "true");
    fireEvent.click(streamSwitch);
    expect(model.setStreaming).toHaveBeenCalledWith(false);
  });

  it("requires explicit bounded snapshot and clip actions", () => {
    const model = controller();
    render(<EnvironmentCameraPanel controller={model} />);

    expect(model.captureSnapshot).not.toHaveBeenCalled();
    expect(model.startClip).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Store camera snapshot" }));
    fireEvent.click(screen.getByRole("button", { name: "Record clip" }));
    expect(model.captureSnapshot).toHaveBeenCalledOnce();
    expect(model.startClip).toHaveBeenCalledWith(10, 3);
  });

  it("opens a bounded preview for camera-owned storage without exposing paths or destructive actions", () => {
    render(<EnvironmentCameraPanel controller={controller()} />);
    fireEvent.click(screen.getByRole("button", { name: /Snapshot/ }));

    expect(screen.getByRole("dialog", { name: "Stored environmental camera media" })).toBeInTheDocument();
    expect(screen.getByAltText("Stored environmental camera snapshot")).toHaveAttribute(
      "src",
      `noob://gateway/environment-media/${media.id}`,
    );
    expect(screen.queryByRole("button", { name: /delete|format/i })).not.toBeInTheDocument();
  });

  it("navigates every stored clip frame through bounded opaque-ID routes", () => {
    render(<EnvironmentCameraPanel controller={controller({ media: [clip] })} />);
    fireEvent.click(screen.getByRole("button", { name: /3s clip/i }));

    const firstFrame = screen.getByAltText("Frame 1 from stored environmental camera clip");
    expect(firstFrame).toHaveAttribute(
      "src",
      `noob://gateway/environment-media/${clip.id}/frames/0`,
    );
    expect(screen.getByRole("button", { name: "Previous stored clip frame" })).toBeDisabled();

    const next = screen.getByRole("button", { name: "Next stored clip frame" });
    fireEvent.click(next);
    expect(screen.getByAltText("Frame 2 from stored environmental camera clip")).toHaveAttribute(
      "src",
      `noob://gateway/environment-media/${clip.id}/frames/1`,
    );
    fireEvent.click(next);
    expect(screen.getByAltText("Frame 3 from stored environmental camera clip")).toHaveAttribute(
      "src",
      `noob://gateway/environment-media/${clip.id}/frames/2`,
    );
    expect(next).toBeDisabled();
    fireEvent.click(next);
    expect(screen.queryByAltText("Frame 4 from stored environmental camera clip")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete|format/i })).not.toBeInTheDocument();
  });
});
