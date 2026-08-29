import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GatewayInputCommand } from "../../shared/gateway-contract";
import { LiveTarget } from "./LiveTarget";

type SendInput = (
  command: GatewayInputCommand,
  recordAction?: boolean,
) => Promise<boolean>;

function interactiveProps(
  sendInput: SendInput,
) {
  return {
    imageSource: "noob://gateway/stream?generation=1&attempt=1",
    live: true,
    claimed: true,
    mode: "human" as const,
    pointerCapture: true,
    pointerLocked: true,
    usingFrameFallback: false,
    onStreamError: vi.fn(),
    onStreamRecovered: vi.fn(),
    sendInput,
  };
}

function installAnimationFrameHarness() {
  let nextId = 1;
  const frames = new Map<number, FrameRequestCallback>();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    const id = nextId;
    nextId += 1;
    frames.set(id, callback);
    return id;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    frames.delete(id);
  });
  return {
    count: () => frames.size,
    runNext: async () => {
      const next = frames.entries().next().value as [number, FrameRequestCallback] | undefined;
      if (next === undefined) return false;
      frames.delete(next[0]);
      act(() => next[1](performance.now()));
      await act(async () => Promise.resolve());
      return true;
    },
  };
}

function movePointer(target: Element, movementX: number, movementY: number) {
  const event = new MouseEvent("mousemove", { bubbles: true });
  Object.defineProperties(event, {
    movementX: { value: movementX },
    movementY: { value: movementY },
  });
  fireEvent(target, event);
}

describe("LiveTarget stream recovery signaling", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

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

  it("coalesces one animation frame into a bounded logical movement", async () => {
    const frames = installAnimationFrameHarness();
    const sendInput = vi.fn<SendInput>().mockResolvedValue(true);
    render(<LiveTarget {...interactiveProps(sendInput)} />);
    const target = screen.getByLabelText("Target display");

    for (let index = 0; index < 200; index += 1) {
      movePointer(target, 1, 0);
    }
    expect(frames.count()).toBe(1);
    await frames.runNext();
    expect(sendInput).toHaveBeenCalledTimes(1);
    expect(sendInput).toHaveBeenLastCalledWith(
      { op: "mouse_move", dx: 200, dy: 0, wheel: 0 },
      false,
    );
  });

  it("preserves motion and button ordering without flooding the input FIFO", async () => {
    const frames = installAnimationFrameHarness();
    const sendInput = vi.fn<SendInput>().mockResolvedValue(true);
    render(<LiveTarget {...interactiveProps(sendInput)} />);
    const target = screen.getByLabelText("Target display");

    movePointer(target, 10, 0);
    await frames.runNext();
    movePointer(target, 20, 0);
    fireEvent.mouseDown(target, { button: 0 });
    movePointer(target, 30, 0);
    fireEvent.mouseUp(target, { button: 0 });
    expect(sendInput).toHaveBeenCalledTimes(5);

    expect(sendInput.mock.calls.map(([command]) => command)).toEqual([
      { op: "mouse_move", dx: 10, dy: 0, wheel: 0 },
      { op: "mouse_move", dx: 20, dy: 0, wheel: 0 },
      { op: "mouse_button", button: "left", event: "down" },
      { op: "mouse_move", dx: 30, dy: 0, wheel: 0 },
      { op: "mouse_button", button: "left", event: "up" },
    ]);
  });

  it("drops accumulated pointer work when control becomes inactive", async () => {
    const frames = installAnimationFrameHarness();
    const sendInput = vi.fn<SendInput>().mockResolvedValue(true);
    const props = interactiveProps(sendInput);
    const { rerender } = render(<LiveTarget {...props} />);
    const target = screen.getByLabelText("Target display");

    movePointer(target, 8, 2);
    expect(frames.count()).toBe(1);
    rerender(<LiveTarget {...props} pointerCapture={false} />);
    expect(frames.count()).toBe(0);
    expect(sendInput).not.toHaveBeenCalled();

    rerender(<LiveTarget {...props} />);
    movePointer(target, 3, -1);
    await frames.runNext();
    expect(sendInput).toHaveBeenCalledTimes(1);
    expect(sendInput).toHaveBeenLastCalledWith(
      { op: "mouse_move", dx: 3, dy: -1, wheel: 0 },
      false,
    );
  });
});
