import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { noobApi } from "../api/noob-client";
import { STREAM_RETRY_MS, useFrameFeed } from "./useFrameFeed";

describe("useFrameFeed restart recovery", () => {
  let objectUrlIndex = 0;

  beforeEach(() => {
    vi.useFakeTimers();
    objectUrlIndex = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => `blob:noob-frame-${objectUrlIndex++}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("issues a fresh stream request when a ready gateway reports zero viewers", () => {
    const { result, unmount } = renderHook(() =>
      useFrameFeed(true, "noob://gateway/stream", 4, 0),
    );
    const initialSource = result.current.imageSource;

    expect(initialSource).toBe("noob://gateway/stream?generation=4&attempt=0");
    act(() => vi.advanceTimersByTime(STREAM_RETRY_MS));

    expect(result.current.usingFrameFallback).toBe(false);
    expect(result.current.imageSource).toBe("noob://gateway/stream?generation=4&attempt=1");
    expect(result.current.imageSource).not.toBe(initialSource);
    unmount();
  });

  it("keeps the still-frame fallback visible until a bounded fresh-stream retry", async () => {
    vi.spyOn(noobApi, "frame").mockResolvedValue({
      bytes: new Uint8Array([0xff, 0xd8, 0xff, 0xd9]),
      contentType: "image/jpeg",
      sequence: "17",
    });
    const { result, unmount } = renderHook(() =>
      useFrameFeed(true, "noob://gateway/stream", 8, 1),
    );

    act(() => result.current.markStreamFailed());
    await act(async () => Promise.resolve());

    expect(result.current.usingFrameFallback).toBe(true);
    expect(result.current.imageSource).toBe("blob:noob-frame-0");
    act(() => vi.advanceTimersByTime(STREAM_RETRY_MS - 1));
    expect(result.current.usingFrameFallback).toBe(true);

    act(() => vi.advanceTimersByTime(1));
    expect(result.current.usingFrameFallback).toBe(false);
    expect(result.current.imageSource).toBe("noob://gateway/stream?generation=8&attempt=1");
    unmount();
  });

  it("does not churn a stream while the gateway reports an attached viewer", () => {
    const { result, unmount } = renderHook(() =>
      useFrameFeed(true, "noob://gateway/stream", 2, 1),
    );
    const initialSource = result.current.imageSource;

    act(() => vi.advanceTimersByTime(STREAM_RETRY_MS * 3));

    expect(result.current.imageSource).toBe(initialSource);
    unmount();
  });

  it("uses the selected environmental source for still-frame fallback", async () => {
    const frame = vi.spyOn(noobApi, "frame").mockResolvedValue({
      bytes: new Uint8Array([0xff, 0xd8, 0xff, 0xd9]),
      contentType: "image/jpeg",
      sequence: "3",
    });
    const { result, unmount } = renderHook(() =>
      useFrameFeed(true, "noob://gateway/environment-stream", 5, 1, "environment"),
    );

    act(() => result.current.markStreamFailed());
    await act(async () => Promise.resolve());

    expect(frame).toHaveBeenCalledWith("environment");
    expect(result.current.imageSource).toBe("blob:noob-frame-0");
    unmount();
  });
});
