import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DeviceManager,
  fingerprintForKeyLine,
  isPrivateOrLocalAddress,
  mergeKnownHostText,
  normalizeDeviceAddress,
  pairingCodeForFingerprint,
  selectDiscoveryAddress,
  sshArguments,
} from "./device-manager.js";

const temporaryDirectories: string[] = [];

async function temporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), "noob-operator-device-test-"));
  temporaryDirectories.push(directory);
  return directory;
}

function profile(seed: number, address = "192.168.50.83") {
  const key = Buffer.alloc(32, seed);
  const hostKeyLine = `${address} ssh-ed25519 ${key.toString("base64")}`;
  const hostKeyFingerprint = fingerprintForKeyLine(hostKeyLine);
  const deviceId = `noob_${createHash("sha256").update(hostKeyFingerprint).digest("hex").slice(0, 24)}`;
  return {
    deviceId,
    profileName: `N.O.O.B. ${seed}`,
    address,
    sshPort: 22,
    hostKeyFingerprint,
    hostKeyLine,
    sshUser: "kali",
    gatewayPort: 8765,
    capabilities: ["target-video", "hid"],
    createdAt: "2026-08-27T12:00:00.000Z",
  };
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("Mac operator device policy", () => {
  it.each([
    "192.168.50.83",
    "10.2.3.4",
    "172.16.5.4",
    "169.254.8.2",
    "noob-uconsole.local",
    "fd00::83",
    "fe80::83%en0",
  ])("accepts private and local device address %s", (address) => {
    expect(isPrivateOrLocalAddress(address)).toBe(true);
    expect(normalizeDeviceAddress(address)).toBe(address);
  });

  it.each([
    "https://192.168.50.83",
    "8.8.8.8",
    "example.com",
    "noob-uconsole",
    "192.168.50.83/path",
    "user@192.168.50.83",
    "fe80::83",
    "",
  ])("rejects non-local or structured manual address %s", (address) => {
    expect(() => normalizeDeviceAddress(address)).toThrow();
  });

  it("prefers RFC1918 IPv4, then ULA IPv6, then hostname and rejects bare link-local hints", () => {
    expect(selectDiscoveryAddress(
      ["fe80::83", "fd00::83", "192.168.50.83"],
      "noob-uconsole.local.",
    )).toBe("192.168.50.83");
    expect(selectDiscoveryAddress(["fe80::83", "fd00::83"], "noob-uconsole.local.")).toBe("fd00::83");
    expect(selectDiscoveryAddress(["fe80::83"], "noob-uconsole.local.")).toBe("noob-uconsole.local");
    expect(selectDiscoveryAddress(["fe80::83"], "")).toBeNull();
    expect(selectDiscoveryAddress(["fe80::83%en0"], "")).toBe("fe80::83%en0");
  });

  it("derives the OpenSSH SHA-256 fingerprint without retaining a private credential", () => {
    const key = Buffer.alloc(32, 7);
    const line = `192.168.50.83 ssh-ed25519 ${key.toString("base64")}`;
    const expected = `SHA256:${createHash("sha256").update(key).digest("base64").replace(/=+$/, "")}`;
    expect(fingerprintForKeyLine(line)).toBe(expected);
  });

  it("derives the same bounded human pairing code from a pinned fingerprint", () => {
    const pinned = profile(17);
    const code = pairingCodeForFingerprint(pinned.hostKeyFingerprint);
    expect(code).toMatch(/^\d{4}-\d{4}$/);
    expect(pairingCodeForFingerprint(pinned.hostKeyFingerprint)).toBe(code);
    expect(() => pairingCodeForFingerprint("SHA256:not-a-fingerprint")).toThrow("invalid_host_key_fingerprint");
  });

  it("merges known-host entries without deleting comments or unrelated pins", () => {
    const existing = `# operator pin\n${profile(18, "192.168.50.40").hostKeyLine}\n`;
    const addition = profile(19, "192.168.50.41").hostKeyLine;
    const merged = mergeKnownHostText(existing, [addition]);
    expect(merged).toContain("# operator pin");
    expect(merged).toContain(profile(18, "192.168.50.40").hostKeyLine);
    expect(merged).toContain(addition);
    expect(mergeKnownHostText(merged, [addition])).toBe(merged);
  });

  it("fails closed instead of replacing a different key for the same known-host endpoint", () => {
    const oldLine = profile(20).hostKeyLine;
    const changedLine = profile(21).hostKeyLine;
    expect(() => mergeKnownHostText(`${oldLine}\n`, [changedLine])).toThrow("known_hosts_key_conflict");
  });

  it("constructs an argument-vector-only pinned tunnel with no token or password material", () => {
    const key = Buffer.alloc(32, 9);
    const line = `192.168.50.83 ssh-ed25519 ${key.toString("base64")}`;
    const profile = {
      deviceId: "noob_0123456789abcdef01234567",
      profileName: "Lab uConsole",
      address: "192.168.50.83",
      sshPort: 22,
      hostKeyFingerprint: fingerprintForKeyLine(line),
      hostKeyLine: line,
      sshUser: "kali",
      gatewayPort: 8765,
      capabilities: ["target-video", "hid"],
      createdAt: "2026-08-27T12:00:00.000Z",
    };
    const args = sshArguments(profile, "/Users/test/.ssh/id_noob", "/Users/test/known_hosts", 23456);
    const joined = args.join(" ");

    expect(args.at(-1)).toBe("kali@192.168.50.83");
    expect(joined).toContain("127.0.0.1:23456:127.0.0.1:8765");
    expect(joined).toContain("BatchMode=yes");
    expect(joined).toContain("IdentitiesOnly=yes");
    expect(joined).toContain("IdentityAgent=none");
    expect(joined).toContain("StrictHostKeyChecking=yes");
    expect(joined).toContain("ExitOnForwardFailure=yes");
    expect(joined).not.toMatch(/token|password|bearer/i);
  });

  it("creates only owner-private non-secret pairing metadata before any connection", async () => {
    const supportDir = await temporaryDirectory();
    const manager = new DeviceManager({
      supportDir,
      identityFile: path.join(supportDir, "missing-identity"),
    });

    await expect(manager.listDevices()).resolves.toEqual({ devices: [], currentDeviceId: null });
    const knownHosts = path.join(supportDir, "known_hosts");
    expect((await stat(supportDir)).mode & 0o077).toBe(0);
    expect((await stat(knownHosts)).mode & 0o077).toBe(0);
    expect(await readFile(knownHosts, "utf8")).toBe("");
    await expect(stat(path.join(supportDir, "gateway.token"))).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("migrates the legacy Electron camelCase store to canonical interoperable v2 without losing the pin", async () => {
    const supportDir = await temporaryDirectory();
    const legacy = profile(22);
    await writeFile(path.join(supportDir, "devices.json"), `${JSON.stringify({ version: 1, devices: [legacy] })}\n`, { mode: 0o600 });
    const manager = new DeviceManager({ supportDir });

    await expect(manager.listDevices()).resolves.toMatchObject({
      devices: [{ deviceId: legacy.deviceId, hostKeyFingerprint: legacy.hostKeyFingerprint }],
    });
    const migrated = JSON.parse(await readFile(path.join(supportDir, "devices.json"), "utf8")) as Record<string, unknown>;
    expect(migrated.version).toBe(2);
    expect(migrated.default_device_id).toBe(legacy.deviceId);
    expect((migrated.devices as Array<Record<string, unknown>>)[0]).toMatchObject({
      device_id: legacy.deviceId,
      profile_name: legacy.profileName,
      host_key_sha256: legacy.hostKeyFingerprint,
    });
    expect((migrated.devices as Array<Record<string, unknown>>)[0]).not.toHaveProperty("deviceId");
  });

  it("migrates the plugin snake_case v1 store and preserves its chosen default", async () => {
    const supportDir = await temporaryDirectory();
    const legacy = profile(23);
    const snake = {
      device_id: legacy.deviceId,
      profile_name: legacy.profileName,
      address: legacy.address,
      ssh_port: legacy.sshPort,
      ssh_user: legacy.sshUser,
      host_key_sha256: legacy.hostKeyFingerprint,
      host_key_line: legacy.hostKeyLine,
      gateway_port: legacy.gatewayPort,
      capabilities: legacy.capabilities,
      created_at: legacy.createdAt,
    };
    await writeFile(path.join(supportDir, "devices.json"), `${JSON.stringify({
      version: 1,
      default_device_id: legacy.deviceId,
      devices: [snake],
    })}\n`, { mode: 0o600 });
    const manager = new DeviceManager({ supportDir });

    await expect(manager.listDevices()).resolves.toMatchObject({ devices: [{ deviceId: legacy.deviceId }] });
    const migrated = JSON.parse(await readFile(path.join(supportDir, "devices.json"), "utf8")) as Record<string, unknown>;
    expect(migrated).toMatchObject({ version: 2, default_device_id: legacy.deviceId });
  });

  it("rejects unsupported future stores and conflicting endpoint pins without rewriting them", async () => {
    const supportDir = await temporaryDirectory();
    const storePath = path.join(supportDir, "devices.json");
    await writeFile(storePath, `${JSON.stringify({ version: 99, default_device_id: null, devices: [] })}\n`, { mode: 0o600 });
    const manager = new DeviceManager({ supportDir });
    await expect(manager.listDevices()).rejects.toThrow("unsupported_device_store_version");
    expect(JSON.parse(await readFile(storePath, "utf8"))).toMatchObject({ version: 99 });

    const first = profile(24);
    const second = profile(25);
    const canonical = (entry: ReturnType<typeof profile>) => ({
      device_id: entry.deviceId,
      profile_name: entry.profileName,
      address: entry.address,
      ssh_port: entry.sshPort,
      ssh_user: entry.sshUser,
      host_key_sha256: entry.hostKeyFingerprint,
      host_key_line: entry.hostKeyLine,
      gateway_port: entry.gatewayPort,
      capabilities: entry.capabilities,
      created_at: entry.createdAt,
    });
    await writeFile(storePath, `${JSON.stringify({
      version: 2,
      default_device_id: first.deviceId,
      devices: [canonical(first), canonical(second)],
    })}\n`, { mode: 0o600 });
    await expect(manager.listDevices()).rejects.toThrow("device_store_conflict");
  });

  it("rejects unbounded discovery before creating a browser", async () => {
    const manager = new DeviceManager({ supportDir: await temporaryDirectory() });
    await expect(manager.discover(10)).rejects.toThrow("invalid_discovery_timeout");
    await expect(manager.discover(6_000)).rejects.toThrow("invalid_discovery_timeout");
  });
});
