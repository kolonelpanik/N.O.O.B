import { LockKeyhole, UnlockKeyhole } from "lucide-react";

interface ControlOwnershipProps {
  authenticated: boolean;
  claimed: boolean;
  claimBlocked: boolean;
  leaseRemainingMs: number | null;
  onClaim(): void;
  onRelease(): void;
}

export function ControlOwnership({
  authenticated,
  claimed,
  claimBlocked,
  leaseRemainingMs,
  onClaim,
  onRelease,
}: ControlOwnershipProps) {
  const countdown = claimed && leaseRemainingMs !== null
    ? `${Math.max(0, leaseRemainingMs / 1_000).toFixed(1)} s`
    : null;

  return (
    <section className="rail-panel ownership-panel" aria-labelledby="ownership-title">
      <div className="rail-panel__heading">
        <h2 id="ownership-title">Control ownership</h2>
        {countdown && <span className="lease-countdown">{countdown}</span>}
      </div>
      <p>{claimed ? "You have control." : "You do not have control."}</p>
      <button
        className={`ownership-action ${claimed ? "ownership-action--release" : ""}`}
        type="button"
        disabled={!authenticated || (!claimed && claimBlocked)}
        onClick={claimed ? onRelease : onClaim}
      >
        {claimed
          ? <UnlockKeyhole size={18} strokeWidth={1.7} />
          : <LockKeyhole size={18} strokeWidth={1.7} />}
        {claimed ? "Release control" : "Take control"}
      </button>
      <p className="ownership-note">
        This Mac's keyboard and pointer are not sent to the target until you take control.
      </p>
    </section>
  );
}
