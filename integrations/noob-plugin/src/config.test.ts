import { createHash } from "node:crypto";
import { chmod, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { addKnownHost, loadStore, mergeKnownHostText, mutateStore } from "./config.js";
import { fingerprintForKeyLine } from "./discovery.js";
import type { DeviceProfile } from "./types.js";

const directories: string[] = [];
const originalSupport = process.env.NOOB_PLUGIN_SUPPORT_DIR;

async function supportDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), "noob-plugin-store-test-"));
  directories.push(directory);
  process.env.NOOB_PLUGIN_SUPPORT_DIR = directory;
  return directory;
}

function profile(seed: number, address = "192.168.50.83"): DeviceProfile {
  const key = Buffer.alloc(32, seed);
  const host_key_line = `${address} ssh-ed25519 ${key.toString("base64")}`;
  const host_key_sha256 = fingerprintForKeyLine(host_key_line);
  return {
    device_id: `noob_${createHash("sha256").update(host_key_sha256).digest("hex").slice(0, 24)}`,
    profile_name: `N.O.O.B. ${seed}`,
    address,
    ssh_port: 22,
    ssh_user: "kali",
    host_key_sha256,
    host_key_line,
    gateway_port: 8765,
    capabilities: ["target-video", "hid"],
    created_at: "2026-08-27T12:00:00.000Z",
  };
}

afterEach(async () => {
  if (originalSupport === undefined) delete process.env.NOOB_PLUGIN_SUPPORT_DIR;
  else process.env.NOOB_PLUGIN_SUPPORT_DIR = originalSupport;
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe.sequential("shared paired-device persistence", () => {
  it("migrates the legacy Electron camelCase store to canonical v2 and chooses its sole pin", async () => {
    const directory = await supportDirectory();
    const pinned = profile(31);
    const legacy = {
      deviceId: pinned.device_id,
      profileName: pinned.profile_name,
      address: pinned.address,
      sshPort: pinned.ssh_port,
      sshUser: pinned.ssh_user,
      hostKeyFingerprint: pinned.host_key_sha256,
      hostKeyLine: pinned.host_key_line,
      gatewayPort: pinned.gateway_port,
      capabilities: pinned.capabilities,
      createdAt: pinned.created_at,
    };
    await writeFile(path.join(directory, "devices.json"), `${JSON.stringify({ version: 1, devices: [legacy] })}\n`, { mode: 0o600 });

    await expect(loadStore()).resolves.toMatchObject({
      version: 2,
      default_device_id: pinned.device_id,
      devices: [{ device_id: pinned.device_id }],
    });
    const migrated = JSON.parse(await readFile(path.join(directory, "devices.json"), "utf8"));
    expect(migrated.devices[0]).toHaveProperty("device_id", pinned.device_id);
    expect(migrated.devices[0]).not.toHaveProperty("deviceId");
  });

  it("serializes concurrent mutations instead of losing either pin", async () => {
    await supportDirectory();
    const first = profile(32, "192.168.50.32");
    const second = profile(33, "192.168.50.33");
    await Promise.all([first, second].map(async (entry) => {
      await mutateStore(async (store) => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        store.devices.push(entry);
        store.default_device_id ??= entry.device_id;
        return store;
      });
    }));
    const store = await loadStore();
    expect(store.devices.map((entry) => entry.device_id).sort()).toEqual([first.device_id, second.device_id].sort());
  });

  it("preserves unrelated known_hosts lines, appends a pin once, and fails closed on endpoint key conflict", async () => {
    const directory = await supportDirectory();
    const unrelated = profile(34, "192.168.50.34");
    const incoming = profile(35, "192.168.50.35");
    const knownHosts = path.join(directory, "known_hosts");
    await writeFile(knownHosts, `# retained\n${unrelated.host_key_line}\n`, { mode: 0o600 });
    await addKnownHost(incoming);
    await addKnownHost(incoming);
    const merged = await readFile(knownHosts, "utf8");
    expect(merged).toContain("# retained");
    expect(merged.match(new RegExp(incoming.host_key_line.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"))).toHaveLength(1);
    expect(() => mergeKnownHostText(merged, [profile(36, incoming.address).host_key_line])).toThrow("known_hosts_key_conflict");

    await chmod(knownHosts, 0o644);
    await expect(addKnownHost(incoming)).rejects.toThrow("known_hosts_permissions_too_open");
  });

  it("rejects conflicting fingerprints pinned to one endpoint", async () => {
    await supportDirectory();
    const first = profile(37);
    const second = profile(38);
    await expect(mutateStore((store) => ({
      version: 2,
      default_device_id: first.device_id,
      devices: [first, second],
    }))).rejects.toThrow("device_store_conflict");
    await expect(loadStore()).resolves.toMatchObject({ devices: [] });
  });
});
