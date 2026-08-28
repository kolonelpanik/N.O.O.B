import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaToolbar } from "./MediaToolbar";

afterEach(cleanup);

function renderToolbar(overrides: Partial<ComponentProps<typeof MediaToolbar>> = {}) {
  const handlers = {
    onFit: vi.fn(),
    onZoomOut: vi.fn(),
    onZoomIn: vi.fn(),
    onScreenshot: vi.fn(),
    onFullscreen: vi.fn(),
  };

  render(
    <MediaToolbar
      fit={false}
      zoomPercent={100}
      fullscreen={false}
      screenshotBusy={false}
      screenshotDisabled={false}
      {...handlers}
      {...overrides}
    />,
  );

  return handlers;
}

describe("MediaToolbar", () => {
  it("routes bounded view and capture controls to their handlers", () => {
    const handlers = renderToolbar();

    fireEvent.click(screen.getByRole("button", { name: "Fit" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Screenshot" }));
    fireEvent.click(screen.getByRole("button", { name: "Fullscreen" }));

    expect(handlers.onFit).toHaveBeenCalledOnce();
    expect(handlers.onZoomOut).toHaveBeenCalledOnce();
    expect(handlers.onZoomIn).toHaveBeenCalledOnce();
    expect(handlers.onScreenshot).toHaveBeenCalledOnce();
    expect(handlers.onFullscreen).toHaveBeenCalledOnce();
  });

  it("disables unsafe capture and zoom-out states and exposes exit fullscreen", () => {
    const handlers = renderToolbar({
      fit: true,
      fullscreen: true,
      screenshotBusy: true,
    });

    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Exit fullscreen" }));
    expect(handlers.onFullscreen).toHaveBeenCalledOnce();
  });
});
