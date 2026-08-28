import { Laptop, Radar, Search, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import type {
  GatewayConfigView,
  GatewayDeviceCandidate,
  GatewayDeviceProfile,
} from "../../shared/gateway-contract";
import { noobApi, OperatorApiError } from "../api/noob-client";

interface DevicePickerProps {
  open: boolean;
  currentConfig: GatewayConfigView;
  onClose(): void;
  onConnected(config: GatewayConfigView): void | Promise<void>;
}

function errorText(error: unknown): string {
  const code = error instanceof OperatorApiError ? error.code : "device_operation_failed";
  return code.replaceAll("_", " ");
}

function CandidateCard({
  candidate,
  busy,
  selected,
  onInspect,
  onSelect,
}: {
  candidate: GatewayDeviceCandidate;
  busy: boolean;
  selected: boolean;
  onInspect(): void;
  onSelect(): void;
}) {
  return (
    <article className={selected ? "device-card device-card--selected" : "device-card"}>
      <span className="device-card__icon"><Laptop size={17} strokeWidth={1.7} /></span>
      <span className="device-card__identity">
        <strong>{candidate.instanceName}</strong>
        <small>{candidate.address}:{candidate.sshPort} · {candidate.source === "manual" ? "Manual" : "Discovered"}</small>
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={candidate.hostKeyFingerprint === null ? onInspect : onSelect}
      >
        {candidate.hostKeyFingerprint === null ? "Inspect" : selected ? "Selected" : "Review"}
      </button>
    </article>
  );
}

export function DevicePicker({ open, currentConfig, onClose, onConnected }: DevicePickerProps) {
  const [known, setKnown] = useState<GatewayDeviceProfile[]>([]);
  const [candidates, setCandidates] = useState<GatewayDeviceCandidate[]>([]);
  const [selected, setSelected] = useState<GatewayDeviceCandidate | null>(null);
  const [address, setAddress] = useState("");
  const [sshPort, setSshPort] = useState("22");
  const [profileName, setProfileName] = useState("My N.O.O.B.");
  const [pairingCodeConfirmation, setPairingCodeConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [scanned, setScanned] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setError(null);
    void noobApi.listDevices()
      .then((result) => {
        if (!cancelled) setKnown(result.devices);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const choose = (candidate: GatewayDeviceCandidate) => {
    setSelected(candidate);
    setPairingCodeConfirmation("");
    setProfileName(candidate.instanceName.slice(0, 64) || "My N.O.O.B.");
    setError(null);
  };

  const discover = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.discoverDevices(2_000);
      setCandidates(result.candidates);
      setScanned(true);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const inspect = async (candidate: GatewayDeviceCandidate) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const resolved = await noobApi.inspectDevice(candidate.candidateId);
      setCandidates((current) => current.map((entry) => entry.candidateId === resolved.candidateId ? resolved : entry));
      choose(resolved);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const probe = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const candidate = await noobApi.probeDevice(address, Number(sshPort));
      setCandidates((current) => [candidate, ...current.filter((entry) => entry.candidateId !== candidate.candidateId)]);
      choose(candidate);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const pair = async () => {
    if (
      busy || selected?.hostKeyFingerprint === null || selected === null ||
      selected.pairingCode === null || pairingCodeConfirmation.trim() !== selected.pairingCode
    ) return;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.pairAndConnectDevice(
        selected.candidateId,
        selected.hostKeyFingerprint,
        profileName,
      );
      await onConnected(result.config);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  const connectKnown = async (device: GatewayDeviceProfile) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await noobApi.connectKnownDevice(device.deviceId);
      await onConnected(result.config);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-layer" role="presentation">
      <section className="device-dialog" role="dialog" aria-modal="true" aria-labelledby="device-title">
        <button className="dialog-close" type="button" aria-label="Close device picker" onClick={onClose}>
          <X size={18} strokeWidth={1.7} />
        </button>
        <div className="device-dialog__title">
          <Radar size={23} strokeWidth={1.7} />
          <span>
            <h2 id="device-title">Connect to a N.O.O.B.</h2>
            <p>Current route: {currentConfig.gatewayUrl}</p>
          </span>
        </div>

        {known.length > 0 && (
          <section className="device-section" aria-labelledby="known-devices-title">
            <div className="device-section__heading">
              <h3 id="known-devices-title">Pinned devices</h3>
              <span>SSH identity previously verified</span>
            </div>
            <div className="device-list">
              {known.map((device) => (
                <article className="device-card" key={device.deviceId}>
                  <span className="device-card__icon"><ShieldCheck size={17} strokeWidth={1.7} /></span>
                  <span className="device-card__identity">
                    <strong>{device.profileName}</strong>
                    <small>{device.address}:{device.sshPort} · {device.hostKeyFingerprint}</small>
                  </span>
                  <button
                    type="button"
                    disabled={busy || currentConfig.currentDeviceId === device.deviceId}
                    onClick={() => void connectKnown(device)}
                  >
                    {currentConfig.currentDeviceId === device.deviceId ? "Connected" : "Connect"}
                  </button>
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="device-section" aria-labelledby="nearby-devices-title">
          <div className="device-section__heading">
            <span>
              <h3 id="nearby-devices-title">Nearby devices</h3>
              <small>Bounded local `_noob-kvm._tcp` discovery</small>
            </span>
            <button className="device-scan" type="button" disabled={busy} onClick={() => void discover()}>
              <Search size={15} strokeWidth={1.8} />
              {busy ? "Working…" : "Scan network"}
            </button>
          </div>
          <p className="device-trust-note">Discovery is an untrusted hint. Pairing still requires comparing the code on the trusted uConsole.</p>
          <div className="device-list">
            {candidates.filter((candidate) => candidate.source === "discovery").map((candidate) => (
              <CandidateCard
                key={candidate.candidateId}
                candidate={candidate}
                busy={busy}
                selected={selected?.candidateId === candidate.candidateId}
                onInspect={() => void inspect(candidate)}
                onSelect={() => choose(candidate)}
              />
            ))}
            {scanned && candidates.every((candidate) => candidate.source !== "discovery") && (
              <p className="device-empty">No advertised device was found. Use the manual address below.</p>
            )}
          </div>
        </section>

        <section className="device-section" aria-labelledby="manual-device-title">
          <div className="device-section__heading">
            <h3 id="manual-device-title">Manual address</h3>
            <span>Private IP or .local hostname only</span>
          </div>
          <form className="device-manual-form" onSubmit={(event) => void probe(event)}>
            <label>
              Device address
              <input
                type="text"
                value={address}
                maxLength={253}
                placeholder="192.168.50.83"
                autoComplete="off"
                spellCheck={false}
                required
                onChange={(event) => setAddress(event.target.value)}
              />
            </label>
            <label>
              SSH port
              <input
                type="number"
                value={sshPort}
                min={1}
                max={65535}
                required
                onChange={(event) => setSshPort(event.target.value)}
              />
            </label>
            <button type="submit" disabled={busy}>Probe</button>
          </form>
        </section>

        {selected?.hostKeyFingerprint !== null && selected !== null && (
          <section className="device-pairing" aria-labelledby="pair-device-title">
            <div className="device-section__heading">
              <h3 id="pair-device-title">Verify and pair</h3>
              <span>{selected.address}:{selected.sshPort}</span>
            </div>
            <p>Compare this short code with the one shown on the trusted uConsole display:</p>
            <output className="device-pairing-code">{selected.pairingCode}</output>
            <details className="device-fingerprint-details">
              <summary>Advanced SSH fingerprint</summary>
              <p>The complete fingerprint is the authoritative identity and must match your trusted record:</p>
              <output className="device-fingerprint">{selected.hostKeyFingerprint}</output>
            </details>
            <div className="device-pairing__fields">
              <label>
                Device name
                <input value={profileName} maxLength={64} required onChange={(event) => setProfileName(event.target.value)} />
              </label>
              <label>
                Confirm pairing code
                <input
                  value={pairingCodeConfirmation}
                  maxLength={9}
                  autoComplete="off"
                  spellCheck={false}
                  inputMode="numeric"
                  placeholder="0000-0000"
                  onChange={(event) => setPairingCodeConfirmation(event.target.value)}
                />
              </label>
            </div>
            <button
              type="button"
              className="device-pair-button"
              disabled={
                busy || profileName.trim().length === 0 || selected.pairingCode === null ||
                pairingCodeConfirmation.trim() !== selected.pairingCode
              }
              onClick={() => void pair()}
            >
              {busy ? "Opening verified tunnel…" : "Pair and connect"}
            </button>
          </section>
        )}

        {error !== null && <p className="dialog-error" role="alert">{error}</p>}
      </section>
    </div>
  );
}
