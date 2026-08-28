import { describe, expect, it } from "vitest";
import type { DeviceProfile } from "./types.js";
import { sshArguments } from "./tunnel.js";

const profile: DeviceProfile = {
  device_id: "noob_0123456789abcdef01234567",
  profile_name: "N.O.O.B. uConsole",
  address: "192.168.0.83",
  ssh_port: 22,
  ssh_user: "kali",
  host_key_sha256: `SHA256:${"a".repeat(43)}`,
  host_key_line: `192.168.0.83 ssh-ed25519 ${Buffer.alloc(32, 1).toString("base64")}`,
  gateway_port: 8765,
  capabilities: ["target-video", "hid"],
  created_at: "2026-08-28T00:00:00.000Z",
};

describe("pinned SSH tunnel arguments", () => {
  it("keeps a space-bearing macOS known-hosts path inside one strict option", () => {
    const knownHosts = "/Users/test/Library/Application Support/N.O.O.B/known_hosts";
    const args = sshArguments(profile, "/Users/test/.ssh/id_noob", knownHosts, 23456);

    expect(args).toContain("StrictHostKeyChecking=yes");
    expect(args).toContain(`UserKnownHostsFile="${knownHosts}"`);
    expect(args).not.toContain(`UserKnownHostsFile=${knownHosts}`);
    expect(args).toContain("ExitOnForwardFailure=yes");
    expect(args.at(-1)).toBe("kali@192.168.0.83");
    expect(args.join(" ")).not.toMatch(/token|password|bearer/i);
  });

  it("fails closed on an OpenSSH config control character in the pinned file path", () => {
    expect(() => sshArguments(profile, "/Users/test/.ssh/id_noob", "/tmp/pins\nother", 23456))
      .toThrow("invalid_known_hosts_path");
  });

  it("escapes literal OpenSSH tokens, quotes, and backslashes in the pinned file path", () => {
    const knownHosts = '/tmp/N.O.O.B %h/"operator"\\known_hosts';
    expect(sshArguments(profile, "/Users/test/.ssh/id_noob", knownHosts, 23456))
      .toContain('UserKnownHostsFile="/tmp/N.O.O.B %%h/\\"operator\\"\\\\known_hosts"');
  });
});
