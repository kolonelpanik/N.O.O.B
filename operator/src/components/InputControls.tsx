import { Crosshair, Keyboard, LockKeyhole, MousePointer2 } from "lucide-react";
import { useRef, useState } from "react";
import type { GatewayInputCommand } from "../../shared/gateway-contract";
import type { OperatorMode } from "../hooks/useOperatorController";
import { validTypeText } from "../input/input-mapping";

interface InputControlsProps {
  claimed: boolean;
  mode: OperatorMode;
  keyboardCapture: boolean;
  pointerCapture: boolean;
  pointerLocked: boolean;
  onToggleKeyboard(): void;
  onTogglePointer(): void;
  onPointerLock(): void;
  sendInput(command: GatewayInputCommand, recordAction?: boolean): Promise<boolean>;
}

export function InputControls({
  claimed,
  mode,
  keyboardCapture,
  pointerCapture,
  pointerLocked,
  onToggleKeyboard,
  onTogglePointer,
  onPointerLock,
  sendInput,
}: InputControlsProps) {
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [textReady, setTextReady] = useState(false);
  const interactive = claimed && mode === "human";

  const sendText = async () => {
    const input = textRef.current;
    if (input === null || !validTypeText(input.value) || !interactive) {
      input?.focus();
      return;
    }
    const text = input.value;
    input.value = "";
    setTextReady(false);
    const sent = await sendInput({ op: "type", text, interval_ms: 0 });
    if (!sent) input.focus();
  };

  return (
    <section className="rail-panel input-panel" aria-labelledby="input-title">
      <div className="rail-panel__heading input-heading">
        <h2 id="input-title">Input controls</h2>
        <Crosshair size={16} strokeWidth={1.7} aria-hidden="true" />
      </div>

      <button
        className="capture-row"
        type="button"
        disabled={!interactive}
        aria-pressed={keyboardCapture}
        onClick={onToggleKeyboard}
      >
        <Keyboard size={18} strokeWidth={1.7} />
        <span>Capture keyboard</span>
        <span className="capture-state">{keyboardCapture ? "Active" : interactive ? "Ready" : "—"}</span>
        <span className={`capture-toggle ${keyboardCapture ? "capture-toggle--on" : ""}`} aria-hidden="true" />
      </button>

      <button
        className="capture-row"
        type="button"
        disabled={!interactive}
        aria-pressed={pointerCapture}
        onClick={onTogglePointer}
      >
        <MousePointer2 size={18} strokeWidth={1.7} />
        <span>Capture pointer</span>
        <span className="capture-state">{pointerCapture ? "Active" : interactive ? "Ready" : "—"}</span>
        <span className={`capture-toggle ${pointerCapture ? "capture-toggle--on" : ""}`} aria-hidden="true" />
      </button>

      <button
        className="pointer-lock"
        type="button"
        disabled={!interactive || !pointerCapture}
        aria-pressed={pointerLocked}
        onClick={onPointerLock}
      >
        <Crosshair size={18} strokeWidth={1.7} />
        <span><strong>Pointer lock</strong><small>Relative mouse on target</small></span>
        <LockKeyhole size={16} strokeWidth={1.7} />
      </button>

      <div className="type-control">
        <div className="type-control__label">
          <label htmlFor="type-text">Type text</label>
          <button
            type="button"
            onClick={() => {
              if (textRef.current) textRef.current.value = "";
              setTextReady(false);
            }}
          >
            Clear
          </button>
        </div>
        <textarea
          id="type-text"
          ref={textRef}
          rows={2}
          maxLength={512}
          disabled={!interactive}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="Type text"
          onInput={(event) => setTextReady(validTypeText(event.currentTarget.value))}
        />
        <button
          className="send-button"
          type="button"
          disabled={!interactive || !textReady}
          onClick={() => void sendText()}
        >
          Send <span aria-hidden="true">⌘↵</span>
        </button>
      </div>
    </section>
  );
}
