import { describe, expect, it } from "vitest";
import { selectDiscoveryAddress } from "./discovery.js";

describe("discovery address selection", () => {
  it("prefers RFC1918 IPv4, then ULA IPv6, then hostname", () => {
    expect(selectDiscoveryAddress(
      ["fe80::83", "fd00::83", "192.168.50.83"],
      "noob-uconsole.local.",
    )).toBe("192.168.50.83");
    expect(selectDiscoveryAddress(["fe80::83", "fd00::83"], "noob-uconsole.local.")).toBe("fd00::83");
    expect(selectDiscoveryAddress(["fe80::83"], "noob-uconsole.local.")).toBe("noob-uconsole.local");
  });

  it("rejects bare link-local IPv6 but retains an explicitly scoped fallback", () => {
    expect(selectDiscoveryAddress(["fe80::83"], "")).toBeNull();
    expect(selectDiscoveryAddress(["fe80::83%en0"], "")).toBe("fe80::83%en0");
  });
});
