import { MonitorCog } from "lucide-react";
import type {
  VideoMode,
  VideoRequestedProfile,
  VideoSignal,
  VideoStatus,
} from "../../shared/gateway-contract";

interface CaptureOutputControlProps {
  video: VideoStatus | null;
  modes: VideoMode[];
  disabled: boolean;
  busy: boolean;
  error: string | null;
  onChange(modeId: string): void;
}

function signalLabel(signal: VideoSignal): string {
  return `${signal.width}×${signal.height} @ ${signal.fps} · ${signal.pixel_format}`;
}

function signalsDiffer(requested: VideoRequestedProfile | null, negotiated: VideoSignal | null): boolean {
  if (requested === null || negotiated === null) return false;
  return requested.width !== negotiated.width ||
    requested.height !== negotiated.height ||
    requested.fps !== negotiated.fps ||
    requested.pixel_format !== negotiated.pixel_format;
}

export function CaptureOutputControl({
  video,
  modes,
  disabled,
  busy,
  error,
  onChange,
}: CaptureOutputControlProps) {
  const validatedModes = modes.filter((mode) => mode.validated);
  const requested = video?.requested ?? null;
  const negotiated = video?.negotiated ?? null;
  const mismatch = signalsDiffer(requested, negotiated);

  return (
    <section className="rail-panel capture-output-panel" aria-labelledby="capture-output-title">
      <div className="rail-panel__heading capture-output-heading">
        <h2 id="capture-output-title"><MonitorCog size={15} strokeWidth={1.7} /> Capture Output</h2>
        <span>
          {busy || video?.state === "switching" || video?.state === "rolling_back"
            ? "Switching"
            : "Manual"}
        </span>
      </div>
      <label className="capture-output-select-label" htmlFor="capture-output-mode">
        Profile
      </label>
      <select
        id="capture-output-mode"
        value={video?.active_mode_id ?? ""}
        disabled={disabled || busy || validatedModes.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {validatedModes.length === 0 && <option value="">No validated profiles</option>}
        {validatedModes.map((mode) => (
          <option key={mode.id} value={mode.id}>
            {mode.label} · {mode.width}×{mode.height} @ {mode.fps}
          </option>
        ))}
      </select>
      {mismatch && requested !== null && negotiated !== null && (
        <dl className="capture-output-negotiation">
          <div><dt>Requested</dt><dd>{signalLabel(requested)}</dd></div>
          <div><dt>Negotiated</dt><dd>{signalLabel(negotiated)}</dd></div>
        </dl>
      )}
      <p className={error === null ? "capture-output-note" : "capture-output-note capture-output-note--error"}>
        {error !== null
          ? error.replaceAll("_", " ")
          : video !== null && (!video.ready || video.state === "degraded")
            ? "Capture output is degraded; choose another validated profile to recover."
          : video?.source_timing_detectable === false
            ? "Target timing is not detectable; choose the capture output manually."
            : "Select the validated capture output for this connection."}
      </p>
    </section>
  );
}
