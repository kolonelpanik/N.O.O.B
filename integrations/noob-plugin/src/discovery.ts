import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import net from "node:net";
import { Bonjour, type Service } from "bonjour-service";
import { assertAddress, isPrivateOrLocalAddress, validAddress } from "./policy.js";
import type { Candidate } from "./types.js";

const CANDIDATE_TTL_MS = 60_000;
const MAX_KEYSCAN_BYTES = 16_384;
const CAPABILITY = /^[a-z0-9-]{1,48}$/;

function candidateId(): string {
  return `candidate_${randomBytes(18).toString("base64url")}`;
}

function expiresAt(): string {
  return new Date(Date.now() + CANDIDATE_TTL_MS).toISOString();
}

export function fingerprintForKeyLine(keyLine: string): string {
  const fields = keyLine.trim().split(/\s+/);
  if (fields.length < 3) throw new Error("invalid_host_key");
  const key = Buffer.from(fields[2] ?? "", "base64");
  if (key.length < 32 || key.length > 16_384) throw new Error("invalid_host_key");
  return `SHA256:${createHash("sha256").update(key).digest("base64").replace(/=+$/, "")}`;
}

export function pairingCodeForFingerprint(fingerprint: string): string {
  if (!/^SHA256:[A-Za-z0-9+/]{43}$/.test(fingerprint)) throw new Error("invalid_host_key_fingerprint");
  const digest = createHash("sha256")
    .update("N.O.O.B. pairing code v1\0", "utf8")
    .update(fingerprint, "ascii")
    .digest();
  const value = digest.readUInt32BE(0) % 100_000_000;
  return value.toString().padStart(8, "0").replace(/^(\d{4})(\d{4})$/, "$1-$2");
}

async function scanKey(address: string, port: number, timeoutMs: number): Promise<{ line: string; fingerprint: string }> {
  assertAddress(address);
  return await new Promise((resolve, reject) => {
    const child = spawn("ssh-keyscan", ["-T", String(Math.max(1, Math.ceil(timeoutMs / 1000))), "-p", String(port), address], {
      stdio: ["ignore", "pipe", "ignore"],
      shell: false,
      env: { PATH: process.env.PATH ?? "/usr/bin:/bin:/usr/sbin:/sbin" },
    });
    let output = "";
    const timer = setTimeout(() => child.kill("SIGTERM"), timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output += chunk;
      if (output.length > MAX_KEYSCAN_BYTES) child.kill("SIGTERM");
    });
    child.once("error", () => {
      clearTimeout(timer);
      reject(new Error("host_key_probe_failed"));
    });
    child.once("close", () => {
      clearTimeout(timer);
      const lines = output.split("\n").map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
      const preferred = lines.find((line) => line.includes(" ssh-ed25519 ")) ?? lines[0];
      if (!preferred) return reject(new Error("host_key_unavailable"));
      try {
        resolve({ line: preferred, fingerprint: fingerprintForKeyLine(preferred) });
      } catch {
        reject(new Error("invalid_host_key"));
      }
    });
  });
}

function txtString(value: unknown, max = 128): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= max ? value : null;
}

function discoveryAddressPreference(value: string): number {
  const ipVersion = net.isIP(value);
  if (ipVersion === 4) {
    const octets = value.split(".").map(Number);
    const first = octets[0] ?? -1;
    const second = octets[1] ?? -1;
    if (first === 10 || (first === 172 && second >= 16 && second <= 31) ||
        (first === 192 && second === 168)) return 0;
  }
  if (ipVersion === 6) {
    const address = value.toLowerCase().split("%", 1)[0] ?? "";
    if (address.startsWith("fc") || address.startsWith("fd")) return 1;
  }
  if (ipVersion === 0) return 2;
  return 3;
}

export function selectDiscoveryAddress(addresses: readonly string[], host?: string): string | null {
  const candidates: Array<{ address: string; preference: number; order: number }> = [];
  for (const [order, raw] of [...addresses, host ?? ""].entries()) {
    const address = raw.trim().replace(/\.$/, "");
    if (!validAddress(address) || !isPrivateOrLocalAddress(address)) continue;
    candidates.push({ address, preference: discoveryAddressPreference(address), order });
  }
  candidates.sort((left, right) => left.preference - right.preference || left.order - right.order);
  return candidates[0]?.address ?? null;
}

function serviceAddress(service: Service): string | null {
  return selectDiscoveryAddress(service.addresses ?? [], service.host);
}

function capabilitiesFromTxt(service: Service): string[] {
  const raw = txtString(service.txt?.capabilities ?? service.txt?.caps, 512);
  if (!raw) return [];
  return [...new Set(raw.split(",").map((value) => value.trim()).filter((value) => CAPABILITY.test(value)))].slice(0, 32);
}

export async function discoverCandidates(timeoutMs: number): Promise<Candidate[]> {
  const bonjour = new Bonjour();
  const found = new Map<string, Candidate>();
  try {
    const browser = bonjour.find({ type: "noob-kvm", protocol: "tcp" }, (service) => {
      const address = serviceAddress(service);
      if (!address || service.port < 1 || service.port > 65535) return;
      try {
        assertAddress(address);
      } catch {
        return;
      }
      const key = `${address}:${service.port}`;
      found.set(key, {
        candidate_id: candidateId(),
        instance_name: service.name.slice(0, 128),
        address,
        ssh_port: service.port,
        observed_host_key_sha256: null,
        pairing_code: null,
        host_key_line: null,
        product: txtString(service.txt?.product),
        version: txtString(service.txt?.version, 64),
        capabilities: capabilitiesFromTxt(service),
        expires_at: expiresAt(),
      });
    });
    await new Promise((resolve) => setTimeout(resolve, timeoutMs));
    browser.stop();
  } finally {
    bonjour.destroy();
  }
  return [...found.values()].slice(0, 32);
}

export async function probeCandidate(address: string, sshPort: number, timeoutMs: number): Promise<Candidate> {
  assertAddress(address);
  const key = await scanKey(address, sshPort, timeoutMs);
  return {
    candidate_id: candidateId(),
    instance_name: `N.O.O.B. at ${address}`,
    address,
    ssh_port: sshPort,
    observed_host_key_sha256: key.fingerprint,
    pairing_code: pairingCodeForFingerprint(key.fingerprint),
    host_key_line: key.line,
    product: "N.O.O.B.",
    version: null,
    capabilities: [],
    expires_at: expiresAt(),
  };
}

export async function resolveCandidate(candidate: Candidate, timeoutMs: number): Promise<Candidate> {
  if (candidate.observed_host_key_sha256 && candidate.host_key_line) return candidate;
  const probed = await probeCandidate(candidate.address, candidate.ssh_port, timeoutMs);
  return {
    ...candidate,
    observed_host_key_sha256: probed.observed_host_key_sha256,
    pairing_code: probed.pairing_code,
    host_key_line: probed.host_key_line,
  };
}

export function candidateExpired(candidate: Candidate): boolean {
  return Date.parse(candidate.expires_at) <= Date.now();
}

export function stableDeviceId(fingerprint: string): string {
  return `noob_${createHash("sha256").update(fingerprint).digest("hex").slice(0, 24)}`;
}
