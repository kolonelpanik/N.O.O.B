import { describe, expect, it, vi } from "vitest";
import {
  applyManagedTokenBestEffort,
  type ManagedTokenClient,
} from "./managed-token.js";

function client() {
  let configured = false;
  const value: ManagedTokenClient & { readonly configured: boolean } = {
    clearToken: vi.fn(() => {
      configured = false;
    }),
    setToken: vi.fn(() => {
      configured = true;
    }),
    get configured() {
      return configured;
    },
  };
  return value;
}

describe("managed token on adopted device connections", () => {
  it.each(["pairAndConnect", "connectKnown"])(
    "authenticates a client adopted from %s without a second launch",
    async () => {
      const target = client();
      const readToken = vi.fn(async () => "x".repeat(48));

      await expect(applyManagedTokenBestEffort(
        target,
        "/owner-private/gateway.token",
        readToken,
      )).resolves.toBe(true);

      expect(readToken).toHaveBeenCalledWith("/owner-private/gateway.token");
      expect(target.setToken).toHaveBeenCalledTimes(1);
      expect(target.clearToken).not.toHaveBeenCalled();
      expect(target.configured).toBe(true);
    },
  );

  it("leaves the newly adopted client unauthenticated when the file is unreadable", async () => {
    const target = client();
    target.setToken("x".repeat(48));
    const readToken = vi.fn(async () => {
      throw new Error("protected_file_unavailable");
    });

    await expect(applyManagedTokenBestEffort(
      target,
      "/owner-private/gateway.token",
      readToken,
    )).resolves.toBe(false);

    expect(target.clearToken).toHaveBeenCalledTimes(1);
    expect(target.configured).toBe(false);
  });
});
