import { describe, expect, it } from "vitest";
import { isPrivateOrLocalAddress, validAddress } from "./policy.js";

describe("device address policy", () => {
  it.each(["192.168.50.83", "10.20.30.40", "172.16.0.1", "169.254.4.2", "noob.local", "fd00::83", "fe80::83%en0"])(
    "allows private or local candidate %s",
    (address) => expect(isPrivateOrLocalAddress(address)).toBe(true),
  );

  it.each(["8.8.8.8", "1.1.1.1", "example.com", "uconsole", "fe80::83"])(
    "does not classify public address %s as local",
    (address) => expect(isPrivateOrLocalAddress(address)).toBe(false),
  );

  it.each(["http://192.168.50.83", "user@host", "192.168.50.0/24", "host;touch-x", "../host"])(
    "rejects decorated address %s",
    (address) => expect(validAddress(address)).toBe(false),
  );
});
