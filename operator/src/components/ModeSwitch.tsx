import { Bot, UserRound } from "lucide-react";
import type { OperatorMode } from "../hooks/useOperatorController";

interface ModeSwitchProps {
  mode: OperatorMode;
  onChange(mode: OperatorMode): void;
}

export function ModeSwitch({ mode, onChange }: ModeSwitchProps) {
  return (
    <section className="rail-panel mode-panel" aria-labelledby="mode-title">
      <h2 id="mode-title">Mode</h2>
      <div className="mode-switch">
        <button
          type="button"
          className={mode === "human" ? "mode-switch__active" : ""}
          aria-pressed={mode === "human"}
          onClick={() => onChange("human")}
        >
          <UserRound size={17} strokeWidth={1.7} /> Human
        </button>
        <button
          type="button"
          className={mode === "agent" ? "mode-switch__active" : ""}
          aria-pressed={mode === "agent"}
          onClick={() => onChange("agent")}
        >
          <Bot size={17} strokeWidth={1.7} /> Agent
        </button>
      </div>
    </section>
  );
}
