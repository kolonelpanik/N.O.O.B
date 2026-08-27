import { Bot, Send } from "lucide-react";
import { useRef, useState } from "react";
import type { GatewayInputCommand } from "../../shared/gateway-contract";
import type { ConnectionState, OperatorMode } from "../hooks/useOperatorController";
import { parseAgentPayload } from "../input/input-mapping";

interface AgentChannelProps {
  authenticated: boolean;
  claimed: boolean;
  connection: ConnectionState;
  mode: OperatorMode;
  lastAction: string;
  sendInput(command: GatewayInputCommand, recordAction?: boolean): Promise<boolean>;
}

export function AgentChannel({
  authenticated,
  claimed,
  connection,
  mode,
  lastAction,
  sendInput,
}: AgentChannelProps) {
  const payloadRef = useRef<HTMLTextAreaElement>(null);
  const [payloadValid, setPayloadValid] = useState(false);
  const ready = authenticated && connection === "live";
  const enabled = ready && claimed && mode === "agent";

  const submit = async () => {
    const input = payloadRef.current;
    if (input === null || !enabled) return;
    const command = parseAgentPayload(input.value);
    if (command === null) {
      input.focus();
      return;
    }
    input.value = "";
    setPayloadValid(false);
    await sendInput(command);
  };

  return (
    <section className="rail-panel agent-panel" aria-labelledby="agent-title">
      <div className="rail-panel__heading agent-heading">
        <h2 id="agent-title"><Bot size={17} strokeWidth={1.7} /> Agent channel</h2>
        <span>{ready ? "API ready" : "—"}</span>
      </div>
      {mode === "agent" && (
        <div className="agent-console">
          <textarea
            ref={payloadRef}
            rows={3}
            disabled={!enabled}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            aria-label="Agent payload"
            placeholder={'{"action":"combo","keys":["GUI","SPACE"]}'}
            onInput={(event) => setPayloadValid(parseAgentPayload(event.currentTarget.value) !== null)}
          />
          <button type="button" disabled={!enabled || !payloadValid} onClick={() => void submit()}>
            <Send size={15} strokeWidth={1.7} /> Send
          </button>
        </div>
      )}
      <div className="last-action">
        <span>Last action</span>
        <strong>{lastAction}</strong>
      </div>
    </section>
  );
}
