import { Laptop, PanelRight, Settings } from "lucide-react";
import { useState, type ReactNode } from "react";
import { ConnectionStatus } from "./ConnectionStatus";
import type { ProofTone } from "../state/proof";

export interface HeaderSignal {
  label: string;
  value: string;
  tone: ProofTone;
}

interface AppShellProps {
  gatewayLabel: string;
  signals: HeaderSignal[];
  workspace: ReactNode;
  controlRail: ReactNode;
  proofRail: ReactNode;
  onDevices(): void;
  onSettings(): void;
}

export function AppShell({
  gatewayLabel,
  signals,
  workspace,
  controlRail,
  proofRail,
  onDevices,
  onSettings,
}: AppShellProps) {
  const [railOpen, setRailOpen] = useState(false);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand" aria-label="N.O.O.B — NEVER OUT OF BOUNDS">
          <span className="brand__mark">N.O.O.B</span>
          <span className="brand__meaning">NEVER OUT OF BOUNDS</span>
        </div>
        <div className="gateway-label">{gatewayLabel}</div>
        <div className="header-signals">
          {signals.map((signal) => <ConnectionStatus key={signal.label} {...signal} />)}
        </div>
        <button className="device-button" type="button" aria-label="Choose N.O.O.B. device" onClick={onDevices}>
          <Laptop size={17} strokeWidth={1.7} />
          <span>Devices</span>
        </button>
        <button
          className="icon-button rail-toggle"
          type="button"
          aria-label="Open control rail"
          aria-expanded={railOpen}
          onClick={() => setRailOpen((open) => !open)}
        >
          <PanelRight size={18} strokeWidth={1.7} />
        </button>
        <button className="icon-button" type="button" aria-label="Gateway settings" onClick={onSettings}>
          <Settings size={19} strokeWidth={1.7} />
        </button>
      </header>

      <main className="app-workspace">
        <section className="live-workspace">{workspace}</section>
        {railOpen && (
          <button
            className="rail-scrim"
            type="button"
            aria-label="Close control rail"
            onClick={() => setRailOpen(false)}
          />
        )}
        <aside className={`control-rail ${railOpen ? "control-rail--open" : ""}`}>
          {controlRail}
        </aside>
      </main>

      {proofRail}
      <footer className="app-footer">Loopback only · SSH tunnel · No automatic recording</footer>
    </div>
  );
}
