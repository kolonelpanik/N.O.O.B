import { describe, expect, it, vi } from "vitest";
import { releaseOwnedLeaseBestEffort, type OwnedLeaseClient } from "./release-ownership.js";

function client(hasLease: boolean, release: () => Promise<unknown>): OwnedLeaseClient {
  return {
    hasLease,
    release,
    clearLease: vi.fn(),
  };
}

describe("passive Electron lease cleanup", () => {
  it("does nothing when this Electron client does not own the active lease", async () => {
    const release = vi.fn(async () => ({ ok: true }));
    const candidate = client(false, release);

    await expect(releaseOwnedLeaseBestEffort(candidate)).resolves.toBe(false);

    expect(release).not.toHaveBeenCalled();
    expect(candidate.clearLease).not.toHaveBeenCalled();
  });

  it("releases only the lease owned by this Electron client", async () => {
    const release = vi.fn(async () => ({ ok: true }));
    const candidate = client(true, release);

    await expect(releaseOwnedLeaseBestEffort(candidate)).resolves.toBe(true);

    expect(release).toHaveBeenCalledTimes(1);
    expect(candidate.clearLease).not.toHaveBeenCalled();
  });

  it("fails closed locally when its owned-lease release fails", async () => {
    const release = vi.fn(async () => Promise.reject(new Error("unavailable")));
    const candidate = client(true, release);

    await expect(releaseOwnedLeaseBestEffort(candidate)).resolves.toBe(false);

    expect(release).toHaveBeenCalledTimes(1);
    expect(candidate.clearLease).toHaveBeenCalledTimes(1);
  });
});
