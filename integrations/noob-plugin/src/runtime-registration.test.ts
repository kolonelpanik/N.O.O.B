import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadStore } from "./config.js";
import { fingerprintForKeyLine, pairingCodeForFingerprint } from "./discovery.js";
import { NoobRuntime } from "./runtime.js";
import type { Candidate } from "./types.js";

const directories: string[] = [];
const runtimes: NoobRuntime[] = [];
const originalSupport = process.env.NOOB_PLUGIN_SUPPORT_DIR;

afterEach(async () => {
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
  if (originalSupport === undefined) delete process.env.NOOB_PLUGIN_SUPPORT_DIR;
  else process.env.NOOB_PLUGIN_SUPPORT_DIR = originalSupport;
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
  vi.restoreAllMocks();
});

describe.sequential("verified device re-registration", () => {
  it("refreshes a moved same-fingerprint appliance, preserves both known-host pins, and connects by its stable id", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "noob-register-test-"));
    directories.push(directory);
    process.env.NOOB_PLUGIN_SUPPORT_DIR = directory;
    const runtime = new NoobRuntime();
    runtimes.push(runtime);
    const candidates = (runtime as unknown as { candidates: Map<string, Candidate> }).candidates;
    const key = Buffer.alloc(32, 47).toString("base64");
    const fingerprint = fingerprintForKeyLine(`192.168.50.47 ssh-ed25519 ${key}`);
    const deviceId = `noob_${createHash("sha256").update(fingerprint).digest("hex").slice(0, 24)}`;
    const candidate = (id: string, address: string, port: number, capabilities: string[]): Candidate => ({
      candidate_id: id,
      instance_name: "N.O.O.B.",
      address,
      ssh_port: port,
      observed_host_key_sha256: fingerprint,
      pairing_code: pairingCodeForFingerprint(fingerprint),
      host_key_line: `${address} ssh-ed25519 ${key}`,
      product: "N.O.O.B.",
      version: "0.2.0",
      capabilities,
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    });

    const firstId = `candidate_${"a".repeat(24)}`;
    candidates.set(firstId, candidate(firstId, "192.168.50.47", 22, ["target-video"]));
    await runtime.register(firstId, fingerprint, "Bench N.O.O.B.", true);

    const movedId = `candidate_${"b".repeat(24)}`;
    candidates.set(movedId, candidate(movedId, "192.168.50.147", 2222, ["target-video", "hid"]));
    await expect(runtime.register(movedId, fingerprint, "Field N.O.O.B.", true)).resolves.toMatchObject({
      device_id: deviceId,
      created: false,
      updated: true,
    });

    await expect(loadStore()).resolves.toMatchObject({
      version: 2,
      default_device_id: deviceId,
      devices: [{
        device_id: deviceId,
        profile_name: "Field N.O.O.B.",
        address: "192.168.50.147",
        ssh_port: 2222,
        capabilities: ["target-video", "hid"],
      }],
    });
    const knownHosts = await readFile(path.join(directory, "known_hosts"), "utf8");
    expect(knownHosts).toContain("192.168.50.47 ssh-ed25519");
    expect(knownHosts).toContain("192.168.50.147 ssh-ed25519");

    const connect = vi.spyOn(runtime.tunnels, "connect").mockResolvedValue({
      device_id: deviceId,
      connection_id: `conn_${"c".repeat(24)}`,
      connection_state: "connected",
      connected_at: "2026-08-27T12:00:00.000Z",
      local_port: 23456,
    });
    await expect(runtime.connect(deviceId)).resolves.toMatchObject({ device_id: deviceId, connection_state: "connected" });
    expect(connect).toHaveBeenCalledWith(deviceId);
  });
});
