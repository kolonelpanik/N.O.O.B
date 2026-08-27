import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LiveTarget } from "./LiveTarget";

describe("LiveTarget stream recovery signaling", () => {
  it("does not mistake a loaded fallback JPEG for a recovered MJPEG stream", () => {
    const onStreamRecovered = vi.fn();
    const props = {
      imageSource: "blob:noob-frame",
      live: true,
      claimed: false,
      mode: "human" as const,
      pointerCapture: false,
      pointerLocked: false,
      usingFrameFallback: true,
      onStreamError: vi.fn(),
      onStreamRecovered,
      sendInput: vi.fn(async () => true),
    };
    const { rerender } = render(<LiveTarget {...props} />);

    fireEvent.load(screen.getByAltText("Live target video"));
    expect(onStreamRecovered).not.toHaveBeenCalled();

    rerender(
      <LiveTarget
        {...props}
        imageSource="noob://gateway/stream?generation=1&attempt=1"
        usingFrameFallback={false}
      />,
    );
    fireEvent.load(screen.getByAltText("Live target video"));
    expect(onStreamRecovered).toHaveBeenCalledTimes(1);
  });
});
