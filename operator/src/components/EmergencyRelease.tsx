import { TriangleAlert } from "lucide-react";

interface EmergencyReleaseProps {
  enabled: boolean;
  onRelease(): void;
}

export function EmergencyRelease({ enabled, onRelease }: EmergencyReleaseProps) {
  return (
    <button
      className="emergency-release"
      type="button"
      disabled={!enabled}
      onClick={onRelease}
    >
      <TriangleAlert size={20} strokeWidth={1.7} />
      RELEASE ALL INPUT
    </button>
  );
}
