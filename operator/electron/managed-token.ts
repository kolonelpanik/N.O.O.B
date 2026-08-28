import { readBearerFromOwnerFile } from "./bootstrap-auth.js";

export interface ManagedTokenClient {
  clearToken(): void;
  setToken(token: string): void;
}

export type ManagedTokenReader = (file: string) => Promise<string>;

/**
 * Apply the owner-provisioned credential to a newly adopted gateway client.
 * A missing, unreadable, or invalid file leaves that client unauthenticated;
 * callers can still present the normal explicit authentication dialog.
 */
export async function applyManagedTokenBestEffort(
  client: ManagedTokenClient,
  tokenFile: string,
  readToken: ManagedTokenReader = readBearerFromOwnerFile,
): Promise<boolean> {
  try {
    client.setToken(await readToken(tokenFile));
    return true;
  } catch {
    client.clearToken();
    return false;
  }
}
