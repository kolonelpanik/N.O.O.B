import { useCallback, useEffect, useRef, useState } from "react";
import { noobApi } from "../api/noob-client";

interface FrameFeed {
  imageSource: string | null;
  usingFrameFallback: boolean;
  markStreamFailed: () => void;
  resetStream: () => void;
}

export const STREAM_RETRY_MS = 2_000;

export function useFrameFeed(
  authenticated: boolean,
  streamUrl: string,
  streamGeneration: number,
  viewerCount: number | null,
): FrameFeed {
  const [usingFrameFallback, setUsingFrameFallback] = useState(false);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [streamAttempt, setStreamAttempt] = useState(0);
  const previousFrameUrl = useRef<string | null>(null);

  useEffect(() => {
    setUsingFrameFallback(false);
    setFrameUrl(null);
    setStreamAttempt(0);
    if (previousFrameUrl.current !== null) {
      URL.revokeObjectURL(previousFrameUrl.current);
      previousFrameUrl.current = null;
    }
  }, [authenticated, streamGeneration, streamUrl]);

  useEffect(() => {
    // Electron can retain a failed MJPEG <img> without firing an error after the
    // gateway process rolls over. Zero server-side viewers is the bounded,
    // content-free liveness signal that forces a genuinely new protocol fetch.
    if (
      !authenticated ||
      streamUrl.length === 0 ||
      (!usingFrameFallback && viewerCount !== 0)
    ) return undefined;

    const timer = window.setTimeout(() => {
      setStreamAttempt((current) => current + 1);
      setUsingFrameFallback(false);
    }, STREAM_RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [authenticated, streamAttempt, streamUrl, usingFrameFallback, viewerCount]);

  useEffect(() => {
    if (!authenticated || !usingFrameFallback) return undefined;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const frame = await noobApi.frame();
        if (cancelled) return;
        const bytes = new Uint8Array(frame.bytes.byteLength);
        bytes.set(frame.bytes);
        const url = URL.createObjectURL(new Blob([bytes.buffer], { type: frame.contentType }));
        const oldUrl = previousFrameUrl.current;
        previousFrameUrl.current = url;
        setFrameUrl(url);
        if (oldUrl !== null) URL.revokeObjectURL(oldUrl);
      } catch {
        // The status surface reports degradation; frame bytes and errors are never logged.
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 400);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [authenticated, usingFrameFallback]);

  useEffect(
    () => () => {
      if (previousFrameUrl.current !== null) {
        URL.revokeObjectURL(previousFrameUrl.current);
      }
    },
    [],
  );

  const markStreamFailed = useCallback(() => setUsingFrameFallback(true), []);
  const resetStream = useCallback(() => setUsingFrameFallback(false), []);
  const streamSource = authenticated && streamUrl
    ? `${streamUrl}${streamUrl.includes("?") ? "&" : "?"}generation=${streamGeneration}&attempt=${streamAttempt}`
    : null;

  return {
    imageSource: usingFrameFallback ? frameUrl : streamSource,
    usingFrameFallback,
    markStreamFailed,
    resetStream,
  };
}
