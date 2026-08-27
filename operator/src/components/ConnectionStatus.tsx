import type { ProofTone } from "../state/proof";

interface ConnectionStatusProps {
  label: string;
  value: string;
  tone: ProofTone;
}

export function ConnectionStatus({ label, value, tone }: ConnectionStatusProps) {
  return (
    <div className="connection-status" aria-label={`${label}: ${value}`}>
      <span className={`status-dot status-dot--${tone}`} aria-hidden="true" />
      <span>{label} · {value}</span>
    </div>
  );
}
