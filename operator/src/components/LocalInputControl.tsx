import { Keyboard, MousePointer2, Power, ShieldAlert } from "lucide-react";
import type { LocalInputStatus } from "../../shared/gateway-contract";
import type { ConnectionState } from "../hooks/useOperatorController";

interface LocalInputControlProps {
  authenticated: boolean;
  connection: ConnectionState;
  status: LocalInputStatus | null;
  controlActive: boolean;
  electronClaimed: boolean;
  busy: boolean;
  actionError: string | null;
  onArm(): void;
  onDisarm(): void;
}

type LocalInputView = "disabled" | "unavailable" | "armed" | "ready" | "error";

const ERROR_COPY: Record<string, string> = {
  exclusive_grab_failed: "The built-in controls could not be exclusively captured.",
  exclusive_ungrab_failed: "The built-in controls may still be captured locally.",
  gateway_unreachable: "The gateway did not confirm the requested state.",
  lease_busy: "Another controller currently owns the input lease.",
  local_input_disabled: "Built-in controls are disabled in gateway configuration.",
  local_input_unavailable: "The built-in keyboard or trackball is unavailable.",
  release_unconfirmed: "Local controls were disarmed, but target input release is unconfirmed.",
};

function safeErrorCopy(code: string): string {
  return ERROR_COPY[code] ?? "The gateway did not confirm the requested local-input state.";
}

export function LocalInputControl({
  authenticated,
  connection,
  status,
  controlActive,
  electronClaimed,
  busy,
  actionError,
  onArm,
  onDisarm,
}: LocalInputControlProps) {
  const fresh = authenticated && connection === "live" && status !== null;
  const inconsistent = status !== null && status.armed !== status.exclusive_grab;
  const errorCode = actionError ?? status?.last_error ?? null;

  let view: LocalInputView;
  let detail: string;
  if (!fresh) {
    view = "unavailable";
    detail = "Gateway status is not current.";
  } else if (!status.enabled) {
    view = "disabled";
    detail = "Enable local input in the gateway configuration to use these controls.";
  } else if (errorCode !== null || inconsistent) {
    view = "error";
    detail = errorCode === null
      ? "Armed and exclusive-capture state disagree. Disarm before continuing."
      : safeErrorCopy(errorCode);
  } else if (status.armed) {
    view = "armed";
    detail = "Built-in controls are exclusively routed to the target.";
  } else if (electronClaimed) {
    view = "unavailable";
    detail = "Release Electron control before arming built-in controls.";
  } else if (controlActive) {
    view = "unavailable";
    detail = "Another controller currently owns the input lease.";
  } else if (!status.ready || !status.keyboard_ready || !status.pointer_ready) {
    view = "unavailable";
    detail = "Waiting for both built-in input devices.";
  } else {
    view = "ready";
    detail = "Keyboard and trackball are ready to arm.";
  }

  const mustDisarm = status?.armed === true || status?.exclusive_grab === true;
  const canDisarm = fresh && status.enabled && mustDisarm && !busy;
  const canArm = view === "ready" && !busy;
  const buttonLabel = busy
    ? mustDisarm ? "Disarming…" : "Arming…"
    : mustDisarm ? "Disarm keyboard + trackball" : "Arm keyboard + trackball";

  return (
    <section
      className={`rail-panel local-input-panel local-input-panel--${view}`}
      aria-labelledby="local-input-title"
      aria-busy={busy}
    >
      <div className="rail-panel__heading local-input-heading">
        <h2 id="local-input-title">uConsole built-in controls</h2>
        <span className={`local-input-state local-input-state--${view}`} aria-live="polite">
          {view[0].toUpperCase() + view.slice(1)}
        </span>
      </div>

      <div className="local-input-devices" aria-label="Built-in device readiness">
        <span className={status?.keyboard_ready ? "local-input-device--ready" : ""}>
          <Keyboard size={15} strokeWidth={1.7} aria-hidden="true" />
          Keyboard {status?.keyboard_ready ? "ready" : "unavailable"}
        </span>
        <span className={status?.pointer_ready ? "local-input-device--ready" : ""}>
          <MousePointer2 size={15} strokeWidth={1.7} aria-hidden="true" />
          Trackball {status?.pointer_ready ? "ready" : "unavailable"}
        </span>
      </div>

      <p className="local-input-detail" aria-live="polite">
        {view === "error" && <ShieldAlert size={15} strokeWidth={1.8} aria-hidden="true" />}
        {detail}
      </p>

      <button
        className={`local-input-action ${mustDisarm ? "local-input-action--disarm" : ""}`}
        type="button"
        disabled={mustDisarm ? !canDisarm : !canArm}
        aria-pressed={mustDisarm}
        onClick={mustDisarm ? onDisarm : onArm}
      >
        <Power size={17} strokeWidth={1.8} aria-hidden="true" />
        {buttonLabel}
      </button>

      <p className="local-input-note">
        While armed, the uConsole controls the target. Press Ctrl+Alt+Esc locally to disarm.
      </p>
    </section>
  );
}
