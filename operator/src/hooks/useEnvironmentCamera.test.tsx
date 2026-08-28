import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  EnvironmentCameraJob,
  EnvironmentCameraMediaItem,
  EnvironmentCameraStatus,
} from "../../shared/gateway-contract";
import { noobApi } from "../api/noob-client";
import { useEnvironmentCamera } from "./useEnvironmentCamera";

const item: EnvironmentCameraMediaItem = {
  id: `m_${"1".repeat(32)}`,
  kind: "snapshot",
  state: "complete",
  created_at: null,
  created_uptime_ms: 2,
  size_bytes: 1000,
  width: 640,
  height: 480,
  frame_count: 1,
  fps: null,
  duration_ms: 0,
  content_type: "image/jpeg",
};

const status: EnvironmentCameraStatus = {
  configured: true,
  reachable: true,
  device_id: `cam_${"2".repeat(16)}`,
  stream_enabled: true,
  sensor_enabled: true,
  sensor_initialized: true,
  power_control: false,
  frame_ready: true,
  generation: 4,
  sequence: 1,
  width: 640,
  height: 480,
  last_frame_age_ms: 10,
  viewers: 1,
  storage: {
    state: "mounted",
    mounted: true,
    writable: true,
    total_bytes: 10_000,
    free_bytes: 8_000,
    reserve_bytes: 500,
    media_count: 1,
    active_job_id: null,
    limits: {
      max_media_items: 20,
      max_total_bytes: 10_000,
      max_clip_duration_ms: 30_000,
      max_clip_fps: 5,
      max_clip_frames: 150,
    },
    last_error: null,
  },
  last_error: null,
};

const jobId = `j_${"3".repeat(32)}`;
const runningJob: EnvironmentCameraJob = {
  job_id: jobId,
  kind: "clip",
  state: "running",
  created_uptime_ms: 10,
  frames_written: 1,
  frames_target: 10,
  media_id: null,
  error_code: null,
};

afterEach(() => vi.restoreAllMocks());

describe("useEnvironmentCamera", () => {
  it("uses the current camera generation for every mutation and never starts recording implicitly", async () => {
    vi.spyOn(noobApi, "listEnvironmentMedia").mockResolvedValue({
      ok: true,
      storage: status.storage,
      items: [item],
      next_cursor: null,
    });
    const setStreaming = vi.spyOn(noobApi, "setEnvironmentCamera").mockResolvedValue({
      ok: true,
      environment_camera: { ...status, stream_enabled: false, sensor_enabled: false, generation: 5 },
    });
    const snapshot = vi.spyOn(noobApi, "captureEnvironmentSnapshot").mockResolvedValue({ ok: true, item });
    const start = vi.spyOn(noobApi, "startEnvironmentClip").mockResolvedValue({
      ok: true,
      job_id: jobId,
      state: "queued",
    });
    vi.spyOn(noobApi, "getEnvironmentClipJob").mockResolvedValue({ ok: true, job: runningJob });
    const stop = vi.spyOn(noobApi, "stopEnvironmentClip").mockResolvedValue({
      ok: true,
      job_id: jobId,
      state: "cancelling",
    });

    const { result, unmount } = renderHook(() => useEnvironmentCamera(true, status));
    await waitFor(() => expect(result.current.media).toEqual([item]));
    expect(start).not.toHaveBeenCalled();

    await act(() => result.current.setStreaming(false));
    expect(setStreaming).toHaveBeenCalledWith(false, 4);

    await act(() => result.current.captureSnapshot());
    expect(snapshot).toHaveBeenCalledWith(5);

    await act(() => result.current.startClip(5, 2));
    expect(start).toHaveBeenCalledWith(5, 2, 5);
    await waitFor(() => expect(result.current.activeJob?.state).toBe("running"));
    await act(() => result.current.stopClip());
    expect(stop).toHaveBeenCalledWith(jobId);
    unmount();
  });

  it("does not issue storage mutations while the camera frame or microSD write path is unavailable", async () => {
    vi.spyOn(noobApi, "listEnvironmentMedia").mockResolvedValue({
      ok: true,
      storage: { ...status.storage, mounted: false, writable: false, state: "absent" },
      items: [],
      next_cursor: null,
    });
    const snapshot = vi.spyOn(noobApi, "captureEnvironmentSnapshot");
    const start = vi.spyOn(noobApi, "startEnvironmentClip");
    const unavailable = {
      ...status,
      frame_ready: false,
      storage: { ...status.storage, mounted: false, writable: false, state: "absent" as const },
    };
    const { result, unmount } = renderHook(() => useEnvironmentCamera(true, unavailable));

    await act(async () => {
      await expect(result.current.captureSnapshot()).resolves.toBeNull();
      await expect(result.current.startClip(10, 3)).resolves.toBe(false);
    });
    expect(snapshot).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
    unmount();
  });

  it("discards an in-flight media page when authentication is cleared", async () => {
    let resolvePage!: (page: Awaited<ReturnType<typeof noobApi.listEnvironmentMedia>>) => void;
    const list = vi.spyOn(noobApi, "listEnvironmentMedia").mockReturnValue(
      new Promise((resolve) => {
        resolvePage = resolve;
      }),
    );
    const { result, rerender, unmount } = renderHook(
      ({ authenticated }) => useEnvironmentCamera(authenticated, status),
      { initialProps: { authenticated: true } },
    );
    await waitFor(() => expect(list).toHaveBeenCalledOnce());

    rerender({ authenticated: false });
    await act(async () => {
      resolvePage({
        ok: true,
        storage: status.storage,
        items: [item],
        next_cursor: null,
      });
      await Promise.resolve();
    });

    expect(result.current.media).toEqual([]);
    expect(result.current.camera).toBeNull();
    expect(result.current.mediaBusy).toBe(false);
    unmount();
  });
});
