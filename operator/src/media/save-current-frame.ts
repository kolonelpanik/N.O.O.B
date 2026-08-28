import type { FrameResult } from "../../shared/gateway-contract";
import { noobApi } from "../api/noob-client";
import type { VideoSource } from "../components/SourceTabs";

type FrameReader = (source: VideoSource) => Promise<FrameResult>;

interface DownloadSurface {
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
  createLink(): Pick<HTMLAnchorElement, "click" | "download" | "href">;
  defer(callback: () => void, delayMs: number): void;
}

function browserDownloadSurface(): DownloadSurface {
  return {
    createObjectURL: (blob) => URL.createObjectURL(blob),
    revokeObjectURL: (url) => URL.revokeObjectURL(url),
    createLink: () => document.createElement("a"),
    defer: (callback, delayMs) => window.setTimeout(callback, delayMs),
  };
}

export async function saveCurrentGatewayFrame(
  source: VideoSource,
  readFrame: FrameReader = (requestedSource) => noobApi.frame(requestedSource),
  surface: DownloadSurface = browserDownloadSurface(),
  capturedAt: Date = new Date(),
): Promise<string> {
  const captured = await readFrame(source);
  const bytes = new Uint8Array(captured.bytes.byteLength);
  bytes.set(captured.bytes);
  const url = surface.createObjectURL(
    new Blob([bytes.buffer], { type: captured.contentType }),
  );
  const filename = `noob-${source}-${capturedAt.toISOString().replaceAll(":", "-")}.jpg`;
  const link = surface.createLink();
  link.href = url;
  link.download = filename;
  link.click();
  surface.defer(() => surface.revokeObjectURL(url), 1_000);
  return filename;
}
