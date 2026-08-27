export interface OwnedLeaseClient {
  readonly hasLease: boolean;
  release(): Promise<unknown>;
  clearLease(): void;
}

/** Release only the lease owned by this client; never invoke the global release endpoint. */
export async function releaseOwnedLeaseBestEffort(client: OwnedLeaseClient): Promise<boolean> {
  if (!client.hasLease) return false;
  try {
    await client.release();
    return true;
  } catch {
    client.clearLease();
    return false;
  }
}
