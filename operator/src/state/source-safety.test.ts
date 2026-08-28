import { describe, expect, it, vi } from "vitest";
import { releaseTargetBeforeObservation } from "./source-safety";

describe("source-switch input safety", () => {
  it("releases remote ownership and local exclusive input before observation mode", async () => {
    const order: string[] = [];
    const safe = await releaseTargetBeforeObservation(
      { claimed: true, localArmed: true },
      {
        releaseRemote: vi.fn(async () => {
          order.push("remote");
          return true;
        }),
        disarmLocal: vi.fn(async () => {
          order.push("local");
          return true;
        }),
      },
    );

    expect(safe).toBe(true);
    expect(order).toEqual(["remote", "local"]);
  });

  it("fails closed when either target input path is not confirmed released", async () => {
    await expect(releaseTargetBeforeObservation(
      { claimed: true, localArmed: false },
      { releaseRemote: async () => false, disarmLocal: async () => true },
    )).resolves.toBe(false);
    await expect(releaseTargetBeforeObservation(
      { claimed: false, localArmed: true },
      { releaseRemote: async () => true, disarmLocal: async () => false },
    )).resolves.toBe(false);
  });

  it("does not mutate an already idle target path", async () => {
    const releaseRemote = vi.fn(async () => true);
    const disarmLocal = vi.fn(async () => true);
    await expect(releaseTargetBeforeObservation(
      { claimed: false, localArmed: false },
      { releaseRemote, disarmLocal },
    )).resolves.toBe(true);
    expect(releaseRemote).not.toHaveBeenCalled();
    expect(disarmLocal).not.toHaveBeenCalled();
  });
});
