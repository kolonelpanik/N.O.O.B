export interface TargetOwnershipSnapshot {
  claimed: boolean;
  localArmed: boolean;
}

export interface TargetReleaseActions {
  releaseRemote(): Promise<boolean>;
  disarmLocal(): Promise<boolean>;
}

export async function releaseTargetBeforeObservation(
  ownership: TargetOwnershipSnapshot,
  actions: TargetReleaseActions,
): Promise<boolean> {
  let remoteReleased = true;
  let localDisarmed = true;
  if (ownership.claimed) remoteReleased = await actions.releaseRemote();
  if (ownership.localArmed) localDisarmed = await actions.disarmLocal();
  return remoteReleased && localDisarmed;
}
