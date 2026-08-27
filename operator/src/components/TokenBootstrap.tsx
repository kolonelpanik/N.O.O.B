import { ShieldCheck, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

interface TokenBootstrapProps {
  open: boolean;
  authenticated: boolean;
  gatewayUrl: string;
  error: string | null;
  onClose(): void;
  onConnect(token: string): Promise<boolean>;
  onClear(): Promise<void>;
}

export function TokenBootstrap({
  open,
  authenticated,
  gatewayUrl,
  error,
  onClose,
  onConnect,
  onClear,
}: TokenBootstrapProps) {
  const tokenRef = useRef<HTMLInputElement>(null);
  const [submitting, setSubmitting] = useState(false);
  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const input = tokenRef.current;
    if (input === null) return;
    const token = input.value;
    input.value = "";
    setSubmitting(true);
    try {
      const connected = await onConnect(token);
      if (!connected) input.focus();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-layer" role="presentation">
      <section className="token-dialog" role="dialog" aria-modal="true" aria-labelledby="token-title">
        <button className="dialog-close" type="button" aria-label="Close" onClick={onClose}>
          <X size={18} strokeWidth={1.7} />
        </button>
        <ShieldCheck size={24} strokeWidth={1.7} className="token-dialog__icon" />
        <h2 id="token-title">Gateway authentication</h2>
        <p className="token-dialog__gateway">{gatewayUrl}</p>
        {authenticated ? (
          <>
            <p>The bearer token is held only in main-process memory.</p>
            <div className="dialog-actions">
              <button type="button" className="dialog-secondary" onClick={onClose}>Done</button>
              <button type="button" className="dialog-danger" onClick={() => void onClear()}>Clear token</button>
            </div>
          </>
        ) : (
          <form onSubmit={(event) => void submit(event)}>
            <label htmlFor="gateway-token">Bearer token</label>
            <input
              id="gateway-token"
              ref={tokenRef}
              type="password"
              minLength={32}
              maxLength={256}
              required
              autoComplete="off"
              spellCheck={false}
              autoFocus
            />
            {error && <p className="dialog-error" role="alert">{error}</p>}
            <div className="dialog-actions">
              <button type="button" className="dialog-secondary" onClick={onClose}>Not now</button>
              <button type="submit" disabled={submitting}>{submitting ? "Connecting…" : "Connect"}</button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
