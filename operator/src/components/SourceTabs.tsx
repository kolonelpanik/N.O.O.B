import { Monitor, Video } from "lucide-react";

export type VideoSource = "target" | "environment";

interface SourceTabsProps {
  source: VideoSource;
  busy: boolean;
  environmentConfigured: boolean;
  error: string | null;
  onChange(source: VideoSource): void;
}

export function SourceTabs({
  source,
  busy,
  environmentConfigured,
  error,
  onChange,
}: SourceTabsProps) {
  return (
    <div className="source-tabs-shell">
      <div className="source-tabs" role="tablist" aria-label="Video source">
        <button
          type="button"
          role="tab"
          aria-selected={source === "target"}
          className={source === "target" ? "source-tab source-tab--active" : "source-tab"}
          disabled={busy}
          onClick={() => onChange("target")}
        >
          <Monitor size={16} strokeWidth={1.8} />
          Target
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={source === "environment"}
          className={source === "environment" ? "source-tab source-tab--active" : "source-tab"}
          disabled={busy || !environmentConfigured}
          onClick={() => onChange("environment")}
        >
          <Video size={16} strokeWidth={1.8} />
          Environment
        </button>
      </div>
      <div className="source-switch-state" aria-live="polite">
        {busy ? "Safely releasing target input…" : error ?? (!environmentConfigured ? "Camera not configured" : "")}
      </div>
    </div>
  );
}
