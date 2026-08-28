import { Camera, Maximize2, Minimize2, Minus, Plus } from "lucide-react";

interface MediaToolbarProps {
  fit: boolean;
  zoomPercent: number;
  fullscreen: boolean;
  screenshotBusy: boolean;
  screenshotDisabled: boolean;
  onFit(): void;
  onZoomOut(): void;
  onZoomIn(): void;
  onScreenshot(): void;
  onFullscreen(): void;
}

export function MediaToolbar({
  fit,
  zoomPercent,
  fullscreen,
  screenshotBusy,
  screenshotDisabled,
  onFit,
  onZoomOut,
  onZoomIn,
  onScreenshot,
  onFullscreen,
}: MediaToolbarProps) {
  return (
    <div className="media-toolbar" aria-label="Video view controls">
      <button
        type="button"
        className={fit ? "media-tool media-tool--active" : "media-tool"}
        aria-pressed={fit}
        onClick={onFit}
      >
        Fit
      </button>
      <span className="zoom-value" aria-label={`Zoom ${zoomPercent} percent`}>
        {zoomPercent}%
      </span>
      <button type="button" className="media-tool media-tool--icon" aria-label="Zoom out" disabled={fit || zoomPercent <= 50} onClick={onZoomOut}>
        <Minus size={15} strokeWidth={1.8} />
      </button>
      <button type="button" className="media-tool media-tool--icon" aria-label="Zoom in" onClick={onZoomIn}>
        <Plus size={15} strokeWidth={1.8} />
      </button>
      <button type="button" className="media-tool media-tool--wide" disabled={screenshotDisabled || screenshotBusy} onClick={onScreenshot}>
        <Camera size={16} strokeWidth={1.7} />
        {screenshotBusy ? "Saving…" : "Screenshot"}
      </button>
      <button type="button" className="media-tool media-tool--wide" onClick={onFullscreen}>
        {fullscreen ? <Minimize2 size={16} strokeWidth={1.7} /> : <Maximize2 size={16} strokeWidth={1.7} />}
        {fullscreen ? "Exit fullscreen" : "Fullscreen"}
      </button>
    </div>
  );
}
