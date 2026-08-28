import {
  Camera,
  CircleStop,
  Film,
  FolderOpen,
  HardDrive,
  RefreshCw,
  Video,
  X,
} from "lucide-react";
import { useState } from "react";
import type { EnvironmentCameraMediaItem } from "../../shared/gateway-contract";
import type { EnvironmentCameraController } from "../hooks/useEnvironmentCamera";

interface EnvironmentCameraPanelProps {
  controller: EnvironmentCameraController;
}

function formatBytes(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value;
  let index = -1;
  do {
    amount /= 1024;
    index += 1;
  } while (amount >= 1024 && index < units.length - 1);
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function formatMediaTime(item: EnvironmentCameraMediaItem): string {
  if (item.created_at === null) return "Camera clock unavailable";
  const value = new Date(item.created_at);
  return Number.isNaN(value.getTime())
    ? "Camera timestamp unavailable"
    : value.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function mediaPreviewUrl(item: EnvironmentCameraMediaItem): string {
  return item.kind === "snapshot"
    ? `noob://gateway/environment-media/${item.id}`
    : `noob://gateway/environment-media/${item.id}/frames/0`;
}

export function EnvironmentCameraPanel({ controller }: EnvironmentCameraPanelProps) {
  const [duration, setDuration] = useState(10);
  const [fps, setFps] = useState(3);
  const [preview, setPreview] = useState<EnvironmentCameraMediaItem | null>(null);
  const camera = controller.camera;
  const storage = controller.storage ?? camera?.storage ?? null;
  const recording = controller.activeJobId !== null;
  const progress = controller.activeJob === null
    ? 0
    : Math.round(controller.activeJob.frames_written / Math.max(1, controller.activeJob.frames_target) * 100);
  const storagePercent = storage === null || storage.total_bytes === null || storage.free_bytes === null || storage.total_bytes <= 0
    ? 0
    : Math.max(0, Math.min(100, Math.round((storage.total_bytes - storage.free_bytes) / storage.total_bytes * 100)));
  const readyToCapture = camera?.frame_ready === true && storage?.mounted === true && storage.writable;

  return (
    <>
      <section className="rail-panel environment-panel" aria-labelledby="environment-camera-title">
        <div className="rail-panel__heading environment-heading">
          <h2 id="environment-camera-title">Environmental camera</h2>
          <span className={camera?.frame_ready ? "camera-state camera-state--live" : "camera-state"}>
            <span className="status-dot" />
            {camera?.frame_ready ? "Live" : camera?.reachable ? "Idle" : "Unavailable"}
          </span>
        </div>

        <div className="camera-stream-row">
          <span>
            <Video size={17} strokeWidth={1.7} />
            <span>
              <strong>Camera stream</strong>
              <small>{camera?.stream_enabled ? "Sensor and stream enabled" : "Sensor and stream disabled"}</small>
            </span>
          </span>
          <button
            type="button"
            className={camera?.stream_enabled ? "camera-toggle camera-toggle--on" : "camera-toggle"}
            role="switch"
            aria-checked={camera?.stream_enabled === true}
            disabled={camera === null || !camera.configured || !camera.reachable || controller.busy}
            onClick={() => void controller.setStreaming(camera?.stream_enabled !== true)}
          >
            {camera?.stream_enabled ? "On" : "Off"}
          </button>
        </div>
        <p className="camera-power-note">
          This controls the camera sensor and network stream. Physical USB power is not switchable and remains on.
        </p>

        <div className="storage-summary">
          <div className="storage-summary__heading">
            <span><HardDrive size={16} strokeWidth={1.7} /> microSD storage</span>
            <strong>{storage?.mounted ? (storage.writable ? "Mounted · writable" : "Mounted · read only") : storage?.state ?? "—"}</strong>
          </div>
          <div className="storage-meter" aria-label={`Storage ${storagePercent} percent used`}>
            <span style={{ width: `${storagePercent}%` }} />
          </div>
          <p>{formatBytes(storage?.free_bytes ?? null)} free of {formatBytes(storage?.total_bytes ?? null)} · {storage?.media_count ?? 0} items</p>
        </div>

        <div className="environment-actions">
          <button type="button" disabled={!readyToCapture || controller.busy} onClick={() => void controller.captureSnapshot()}>
            <Camera size={16} strokeWidth={1.7} />
            Store camera snapshot
          </button>
          {recording ? (
            <button type="button" className="record-action record-action--stop" disabled={controller.busy} onClick={() => void controller.stopClip()}>
              <CircleStop size={16} strokeWidth={1.7} />
              Stop clip
            </button>
          ) : (
            <button type="button" className="record-action" disabled={!readyToCapture || controller.busy} onClick={() => void controller.startClip(duration, fps)}>
              <Film size={16} strokeWidth={1.7} />
              Record clip
            </button>
          )}
        </div>

        <div className="clip-settings" aria-label="Clip settings">
          <label>
            Duration
            <select value={duration} disabled={recording || controller.busy} onChange={(event) => setDuration(Number(event.target.value))}>
              {[5, 10, 15, 20, 30].map((value) => <option key={value} value={value}>{value}s</option>)}
            </select>
          </label>
          <label>
            Rate
            <select value={fps} disabled={recording || controller.busy} onChange={(event) => setFps(Number(event.target.value))}>
              {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value} fps</option>)}
            </select>
          </label>
        </div>

        <div className="recording-state" aria-live="polite">
          <span>
            <span className={recording ? "recording-dot recording-dot--active" : "recording-dot"} />
            {controller.activeJob?.state ?? (recording ? "Queued" : "Idle")}
          </span>
          <strong>{recording ? `${progress}% · ${controller.activeJob?.frames_written ?? 0}/${controller.activeJob?.frames_target ?? "—"} frames` : "No automatic recording"}</strong>
        </div>

        <div className="media-library-heading">
          <span><FolderOpen size={16} strokeWidth={1.7} /> Recent media</span>
          <button type="button" aria-label="Refresh recent media" disabled={controller.mediaBusy} onClick={() => void controller.refreshMedia()}>
            <RefreshCw size={15} strokeWidth={1.7} />
          </button>
        </div>
        <div className="media-library">
          {controller.media.length === 0 ? (
            <p className="media-empty">{controller.mediaBusy ? "Loading camera storage…" : "No stored camera media"}</p>
          ) : controller.media.slice(0, 6).map((item) => (
            <button key={item.id} type="button" className="media-item" onClick={() => setPreview(item)}>
              <img src={mediaPreviewUrl(item)} alt="" />
              <span>
                <strong>{item.kind === "snapshot" ? "Snapshot" : `${item.duration_ms / 1000}s clip`}</strong>
                <small>{formatMediaTime(item)}</small>
                <small>{item.width} × {item.height} · {formatBytes(item.size_bytes)}</small>
              </span>
            </button>
          ))}
          {controller.nextCursor !== null && (
            <button type="button" className="media-load-more" disabled={controller.mediaBusy} onClick={() => void controller.loadMore()}>
              Load more
            </button>
          )}
        </div>

        {controller.error !== null && (
          <p className="environment-error" role="alert">{controller.error.replaceAll("_", " ")}</p>
        )}
      </section>

      {preview !== null && (
        <div className="media-preview-layer" role="dialog" aria-modal="true" aria-label="Stored environmental camera media">
          <div className="media-preview">
            <div className="media-preview__heading">
              <span>{preview.kind === "snapshot" ? "Stored snapshot" : "Stored clip · first frame"}</span>
              <button type="button" aria-label="Close media preview" onClick={() => setPreview(null)}><X size={18} /></button>
            </div>
            <img src={mediaPreviewUrl(preview)} alt={preview.kind === "snapshot" ? "Stored environmental camera snapshot" : "First frame from stored environmental camera clip"} />
            <p>{formatMediaTime(preview)} · {preview.width} × {preview.height} · {formatBytes(preview.size_bytes)}</p>
          </div>
        </div>
      )}
    </>
  );
}
