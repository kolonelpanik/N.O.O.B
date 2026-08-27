import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VideoMode, VideoStatus } from "../../shared/gateway-contract";
import { CaptureOutputControl } from "./CaptureOutputControl";

afterEach(cleanup);

const MODES: VideoMode[] = [
  {
    id: "hd",
    label: "HD",
    width: 1280,
    height: 720,
    fps: 30,
    pixel_format: "MJPG",
    max_frame_bytes: 2_097_152,
    validated: true,
  },
  {
    id: "lab-only",
    label: "Lab only",
    width: 2560,
    height: 1600,
    fps: 30,
    pixel_format: "MJPG",
    max_frame_bytes: 8_388_608,
    validated: false,
  },
  {
    id: "full-hd",
    label: "Full HD",
    width: 1920,
    height: 1080,
    fps: 30,
    pixel_format: "MJPG",
    max_frame_bytes: 4_194_304,
    validated: true,
  },
];

const VIDEO: VideoStatus = {
  state: "ready",
  generation: 3,
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
  negotiated: { width: 1280, height: 720, fps: 20, pixel_format: "MJPG" },
  source_timing_detectable: false,
  ready: true,
  device: "/dev/noob-video",
  width: 1280,
  height: 720,
  fps: 20,
  last_frame_age_ms: 4,
  sequence: 10,
  restarts: 0,
  viewers: 1,
  last_error: null,
};

describe("CaptureOutputControl", () => {
  it("shows only validated profiles and reports requested versus negotiated output", () => {
    render(
      <CaptureOutputControl
        video={VIDEO}
        modes={MODES}
        disabled={false}
        busy={false}
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: /^HD ·/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Lab only/ })).not.toBeInTheDocument();
    expect(screen.getByText("1280×720 @ 30 · MJPG")).toBeInTheDocument();
    expect(screen.getByText("1280×720 @ 20 · MJPG")).toBeInTheDocument();
    expect(screen.getByText(/Target timing is not detectable/)).toBeInTheDocument();
    expect(screen.queryByText(/4K/i)).not.toBeInTheDocument();
  });

  it("forwards a validated selection and obeys the disabled safety gate", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CaptureOutputControl
        video={VIDEO}
        modes={MODES}
        disabled={false}
        busy={false}
        error={null}
        onChange={onChange}
      />,
    );
    const select = screen.getByLabelText("Profile") as HTMLSelectElement;
    expect(select).not.toBeDisabled();
    fireEvent.change(select, { target: { value: "full-hd" } });
    expect(onChange).toHaveBeenCalledWith("full-hd");

    rerender(
      <CaptureOutputControl
        video={VIDEO}
        modes={MODES}
        disabled
        busy={false}
        error={null}
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("Profile")).toBeDisabled();
  });

  it("keeps recovery profiles available when the current capture is degraded", () => {
    render(
      <CaptureOutputControl
        video={{ ...VIDEO, ready: false, state: "degraded" }}
        modes={MODES}
        disabled={false}
        busy={false}
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Profile")).toBeEnabled();
    expect(screen.getByText(/choose another validated profile to recover/)).toBeVisible();
  });

  it("renders rollback as the same in-progress state as switching", () => {
    render(
      <CaptureOutputControl
        video={{ ...VIDEO, state: "rolling_back" }}
        modes={MODES}
        disabled
        busy={false}
        error={null}
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Switching")).toBeVisible();
    expect(screen.getByLabelText("Profile")).toBeDisabled();
  });
});
