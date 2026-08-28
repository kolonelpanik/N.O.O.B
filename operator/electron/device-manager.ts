import { createHash, randomBytes } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { access, chmod, lstat, mkdir, readFile, rename, rmdir, stat, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { Bonjour, type Service } from "bonjour-service";
import type {
  GatewayDeviceCandidate,
  GatewayDeviceProfile,
  GatewayDeviceSource,
} from "../shared/gateway-contract.js";

const HOSTNAME_PATTERN = /^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;
const CANDIDATE_ID_PATTERN = /^candidate_[A-Za-z0-9_-]{16,128}$/;
const DEVICE_ID_PATTERN = /^noob_[a-z0-9]{16,64}$/;
const FINGERPRINT_PATTERN = /^SHA256:[A-Za-z0-9+/]{43}$/;
const SSH_USER_PATTERN = /^[a-z_][a-z0-9_-]{0,31}$/i;
const CAPABILITY_PATTERN = /^[a-z0-9-]{1,48}$/;
const CANDIDATE_TTL_MS = 60_000;
const MAX_KEYSCAN_BYTES = 16_384;
const CONNECT_TIMEOUT_MS = 8_000;
const MAX_DEVICES = 64;
const SSH_EXECUTABLE = "/usr/bin/ssh";
const SSH_KEYSCAN_EXECUTABLE = "/usr/bin/ssh-keyscan";
const CHILD_PROCESS_PATH = "/usr/bin:/bin:/usr/sbin:/sbin";
const IPV6_SCOPE_PATTERN = /^[A-Za-z0-9_.-]{1,32}$/;
const DEVICE_STORE_VERSION = 2;
const FILE_LOCK_RETRY_MS = 20;
const FILE_LOCK_ATTEMPTS = 250;
const STALE_FILE_LOCK_MS = 30_000;

interface CandidateRecord extends GatewayDeviceCandidate {
  hostKeyLine: string | null;
}

interface StoredProfile extends GatewayDeviceProfile {
  hostKeyLine: string;
  sshUser: string;
  gatewayPort: number;
}

interface DeviceStore {
  version: 2;
  defaultDeviceId: string | null;
  devices: StoredProfile[];
}

interface ParsedDeviceStore {
  store: DeviceStore;
  migrated: boolean;
}

export interface DeviceManagerOptions {
  supportDir: string;
  identityFile?: string;
  sshUser?: string;
  remoteGatewayPort?: number;
}

export interface DeviceConnection {
  device: GatewayDeviceProfile;
  gatewayUrl: string;
}

interface LiveTunnel {
  child: ChildProcess;
  deviceId: string;
  gatewayUrl: string;
}

function boundedText(value: unknown, max: number): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= max ? value : null;
}

function candidateId(): string {
  return `candidate_${randomBytes(18).toString("base64url")}`;
}

function expiry(): string {
  return new Date(Date.now() + CANDIDATE_TTL_MS).toISOString();
}

function stableDeviceId(fingerprint: string): string {
  return `noob_${createHash("sha256").update(fingerprint).digest("hex").slice(0, 24)}`;
}

/**
 * Eight decimal digits are short enough to compare on the appliance display,
 * while the complete OpenSSH fingerprint remains the authoritative advanced
 * identity. The domain separator makes this representation stable and
 * independent of unrelated fingerprint displays.
 */
export function pairingCodeForFingerprint(fingerprint: string): string {
  if (!FINGERPRINT_PATTERN.test(fingerprint)) throw new Error("invalid_host_key_fingerprint");
  const digest = createHash("sha256")
    .update("N.O.O.B. pairing code v1\0", "utf8")
    .update(fingerprint, "ascii")
    .digest();
  const value = digest.readUInt32BE(0) % 100_000_000;
  return value.toString().padStart(8, "0").replace(/^(\d{4})(\d{4})$/, "$1-$2");
}

export function isPrivateOrLocalAddress(value: string): boolean {
  const ipVersion = net.isIP(value);
  if (ipVersion === 0) {
    return value.toLowerCase().endsWith(".local");
  }
  if (ipVersion === 4) {
    const octets = value.split(".").map(Number);
    const first = octets[0] ?? -1;
    const second = octets[1] ?? -1;
    return first === 10 || first === 127 ||
      (first === 169 && second === 254) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 192 && second === 168);
  }
  const normalized = value.toLowerCase();
  const scopeIndex = normalized.indexOf("%");
  const address = scopeIndex === -1 ? normalized : normalized.slice(0, scopeIndex);
  const scope = scopeIndex === -1 ? null : normalized.slice(scopeIndex + 1);
  const linkLocal = address.startsWith("fe8") || address.startsWith("fe9") ||
    address.startsWith("fea") || address.startsWith("feb");
  if (linkLocal) return scope !== null && IPV6_SCOPE_PATTERN.test(scope);
  if (scope !== null) return false;
  return address === "::1" || address.startsWith("fc") || address.startsWith("fd");
}

export function normalizeDeviceAddress(raw: string): string {
  const value = raw.trim().replace(/\.$/, "");
  if (value.length === 0 || value.length > 253 || value.includes("\0")) {
    throw new Error("invalid_device_address");
  }
  if (net.isIP(value) === 0 && !HOSTNAME_PATTERN.test(value)) {
    throw new Error("invalid_device_address");
  }
  if (!isPrivateOrLocalAddress(value)) throw new Error("public_device_address_blocked");
  return value;
}

function validPort(value: number): number {
  if (!Number.isSafeInteger(value) || value < 1 || value > 65_535) {
    throw new Error("invalid_ssh_port");
  }
  return value;
}

function validProfileName(value: string): string {
  const name = value.trim();
  const hasControlCharacter = [...name].some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint <= 0x1f || codePoint === 0x7f;
  });
  if (name.length < 1 || name.length > 64 || hasControlCharacter) {
    throw new Error("invalid_profile_name");
  }
  return name;
}

export function fingerprintForKeyLine(keyLine: string): string {
  const fields = keyLine.trim().split(/\s+/);
  if (fields.length < 3) throw new Error("invalid_host_key");
  const key = Buffer.from(fields[2] ?? "", "base64");
  if (key.length < 32 || key.length > 16_384) throw new Error("invalid_host_key");
  return `SHA256:${createHash("sha256").update(key).digest("base64").replace(/=+$/, "")}`;
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
    try {
      const address = normalizeDeviceAddress(raw);
      candidates.push({ address, preference: discoveryAddressPreference(address), order });
    } catch {
      // Ignore public, malformed, and bare link-local discovery hints.
    }
  }
  candidates.sort((left, right) => left.preference - right.preference || left.order - right.order);
  return candidates[0]?.address ?? null;
}

function serviceAddress(service: Service): string | null {
  return selectDiscoveryAddress(service.addresses ?? [], service.host);
}

function serviceCapabilities(service: Service): string[] {
  const raw = boundedText(service.txt?.capabilities ?? service.txt?.caps, 512);
  if (raw === null) return [];
  return [...new Set(raw.split(",").map((entry) => entry.trim()).filter((entry) => CAPABILITY_PATTERN.test(entry)))].slice(0, 32);
}

async function ownerOnlyRegularFile(file: string): Promise<void> {
  let info;
  try {
    info = await stat(file);
  } catch {
    throw new Error("ssh_identity_unavailable");
  }
  if (!info.isFile()) throw new Error("ssh_identity_not_regular");
  if ((info.mode & 0o077) !== 0) throw new Error("ssh_identity_permissions_too_open");
}

async function freeLoopbackPort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = typeof address === "object" && address !== null ? address.port : 0;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function waitForGateway(gatewayUrl: string, child: ChildProcess): Promise<void> {
  const deadline = Date.now() + CONNECT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("ssh_tunnel_exited");
    try {
      const response = await fetch(`${gatewayUrl}/healthz`, {
        method: "GET",
        cache: "no-store",
        signal: AbortSignal.timeout(750),
      });
      if (response.ok) return;
    } catch {
      // The tunnel or gateway may still be starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("gateway_tunnel_timeout");
}

function publicProfile(profile: StoredProfile): GatewayDeviceProfile {
  return {
    deviceId: profile.deviceId,
    profileName: profile.profileName,
    address: profile.address,
    sshPort: profile.sshPort,
    hostKeyFingerprint: profile.hostKeyFingerprint,
    capabilities: [...profile.capabilities],
    createdAt: profile.createdAt,
  };
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  const accepted = new Set(allowed);
  return Object.keys(value).every((key) => accepted.has(key));
}

function parseStoredProfile(value: unknown, format: "canonical" | "electron-v1"): StoredProfile {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("invalid_device_store");
  const profile = value as Record<string, unknown>;
  const canonical = format === "canonical";
  const allowed = canonical
    ? [
      "device_id", "profile_name", "address", "ssh_port", "ssh_user",
      "host_key_sha256", "host_key_line", "gateway_port", "capabilities", "created_at",
    ]
    : [
      "deviceId", "profileName", "address", "sshPort", "sshUser",
      "hostKeyFingerprint", "hostKeyLine", "gatewayPort", "capabilities", "createdAt",
    ];
  if (!hasOnlyKeys(profile, allowed)) throw new Error("invalid_device_store");
  const deviceId = boundedText(canonical ? profile.device_id : profile.deviceId, 80);
  const profileName = boundedText(canonical ? profile.profile_name : profile.profileName, 64);
  const address = boundedText(profile.address, 253);
  const hostKeyFingerprint = boundedText(canonical ? profile.host_key_sha256 : profile.hostKeyFingerprint, 80);
  const hostKeyLine = boundedText(canonical ? profile.host_key_line : profile.hostKeyLine, 4096);
  const sshUser = boundedText(canonical ? profile.ssh_user : profile.sshUser, 32);
  const createdAt = boundedText(canonical ? profile.created_at : profile.createdAt, 64);
  const sshPortValue = canonical ? profile.ssh_port : profile.sshPort;
  const gatewayPortValue = canonical ? profile.gateway_port : profile.gatewayPort;
  if (
    deviceId === null || !DEVICE_ID_PATTERN.test(deviceId) ||
    profileName === null || address === null || hostKeyFingerprint === null ||
    !FINGERPRINT_PATTERN.test(hostKeyFingerprint) || hostKeyLine === null ||
    sshUser === null || !SSH_USER_PATTERN.test(sshUser) || createdAt === null ||
    Number.isNaN(Date.parse(createdAt)) || typeof sshPortValue !== "number" ||
    typeof gatewayPortValue !== "number"
  ) throw new Error("invalid_device_store");
  normalizeDeviceAddress(address);
  const sshPort = validPort(sshPortValue);
  const gatewayPort = validPort(gatewayPortValue);
  if (!Array.isArray(profile.capabilities) || profile.capabilities.length > 32) throw new Error("invalid_device_store");
  const capabilities = [...new Set(profile.capabilities.map((entry) => {
    if (typeof entry !== "string" || !CAPABILITY_PATTERN.test(entry)) throw new Error("invalid_device_store");
    return entry;
  }))];
  if (fingerprintForKeyLine(hostKeyLine) !== hostKeyFingerprint) throw new Error("invalid_device_store");
  if (stableDeviceId(hostKeyFingerprint) !== deviceId) throw new Error("invalid_device_store");
  return {
    deviceId,
    profileName: validProfileName(profileName),
    address,
    sshPort,
    hostKeyFingerprint,
    hostKeyLine,
    sshUser,
    gatewayPort,
    capabilities,
    createdAt,
  };
}

function assertStoreInvariants(store: DeviceStore): DeviceStore {
  if (store.devices.length > MAX_DEVICES) throw new Error("device_store_full");
  const deviceIds = new Set<string>();
  const fingerprints = new Set<string>();
  const endpoints = new Map<string, string>();
  for (const profile of store.devices) {
    if (deviceIds.has(profile.deviceId) || fingerprints.has(profile.hostKeyFingerprint)) {
      throw new Error("device_store_conflict");
    }
    deviceIds.add(profile.deviceId);
    fingerprints.add(profile.hostKeyFingerprint);
    const endpoint = `${profile.address.toLowerCase()}\0${profile.sshPort}`;
    const pinnedFingerprint = endpoints.get(endpoint);
    if (pinnedFingerprint !== undefined && pinnedFingerprint !== profile.hostKeyFingerprint) {
      throw new Error("device_store_conflict");
    }
    endpoints.set(endpoint, profile.hostKeyFingerprint);
  }
  if (store.defaultDeviceId !== null && !deviceIds.has(store.defaultDeviceId)) {
    throw new Error("invalid_device_store");
  }
  return store;
}

function parseDeviceStore(value: unknown): ParsedDeviceStore {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("invalid_device_store");
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.devices) || record.devices.length > MAX_DEVICES) throw new Error("invalid_device_store");
  if (record.version === DEVICE_STORE_VERSION) {
    if (!hasOnlyKeys(record, ["version", "default_device_id", "devices"])) throw new Error("invalid_device_store");
    const defaultDeviceId = record.default_device_id;
    if (defaultDeviceId !== null && (typeof defaultDeviceId !== "string" || !DEVICE_ID_PATTERN.test(defaultDeviceId))) {
      throw new Error("invalid_device_store");
    }
    return {
      store: assertStoreInvariants({
        version: 2,
        defaultDeviceId,
        devices: record.devices.map((entry) => parseStoredProfile(entry, "canonical")),
      }),
      migrated: false,
    };
  }
  if (record.version !== 1) throw new Error("unsupported_device_store_version");
  const isPluginV1 = Object.prototype.hasOwnProperty.call(record, "default_device_id");
  const allowed = isPluginV1
    ? ["version", "default_device_id", "devices"]
    : ["version", "devices"];
  if (!hasOnlyKeys(record, allowed)) throw new Error("invalid_device_store");
  const defaultDeviceId = isPluginV1 ? record.default_device_id : null;
  if (defaultDeviceId !== null && (typeof defaultDeviceId !== "string" || !DEVICE_ID_PATTERN.test(defaultDeviceId))) {
    throw new Error("invalid_device_store");
  }
  const devices = record.devices.map((entry) => parseStoredProfile(entry, isPluginV1 ? "canonical" : "electron-v1"));
  return {
    store: assertStoreInvariants({
      version: 2,
      defaultDeviceId: defaultDeviceId ?? (devices.length === 1 ? devices[0]?.deviceId ?? null : null),
      devices,
    }),
    migrated: true,
  };
}

function canonicalStore(store: DeviceStore): object {
  const validated = assertStoreInvariants(store);
  return {
    version: DEVICE_STORE_VERSION,
    default_device_id: validated.defaultDeviceId,
    devices: validated.devices.map((profile) => ({
      device_id: profile.deviceId,
      profile_name: profile.profileName,
      address: profile.address,
      ssh_port: profile.sshPort,
      ssh_user: profile.sshUser,
      host_key_sha256: profile.hostKeyFingerprint,
      host_key_line: profile.hostKeyLine,
      gateway_port: profile.gatewayPort,
      capabilities: [...profile.capabilities],
      created_at: profile.createdAt,
    })),
  };
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function withFileLock<T>(target: string, operation: () => Promise<T>): Promise<T> {
  const lockDirectory = `${target}.lock`;
  let locked = false;
  for (let attempt = 0; attempt < FILE_LOCK_ATTEMPTS; attempt += 1) {
    try {
      await mkdir(lockDirectory, { mode: 0o700 });
      locked = true;
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      try {
        const lockInfo = await lstat(lockDirectory);
        if (!lockInfo.isDirectory()) throw new Error("device_store_lock_invalid");
        if (Date.now() - lockInfo.mtimeMs > STALE_FILE_LOCK_MS) {
          await rmdir(lockDirectory);
          continue;
        }
      } catch (inspectionError) {
        if ((inspectionError as NodeJS.ErrnoException).code === "ENOENT") continue;
        throw inspectionError;
      }
      await delay(FILE_LOCK_RETRY_MS);
    }
  }
  if (!locked) throw new Error("device_store_busy");
  let result: T | undefined;
  let failed = false;
  let failure: unknown;
  try {
    result = await operation();
  } catch (error) {
    failed = true;
    failure = error;
  }
  try {
    await rmdir(lockDirectory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT" && !failed) throw error;
  }
  if (failed) throw failure;
  return result as T;
}

function knownHostIdentity(line: string): { hosts: string[]; algorithm: string; key: string } | null {
  const value = line.trim();
  if (value.length === 0 || value.startsWith("#") || value.startsWith("|")) return null;
  const fields = value.split(/\s+/);
  const offset = fields[0]?.startsWith("@") ? 1 : 0;
  const hosts = fields[offset]?.split(",").filter(Boolean) ?? [];
  const algorithm = fields[offset + 1] ?? "";
  const key = fields[offset + 2] ?? "";
  if (hosts.length === 0 || algorithm.length === 0 || key.length === 0) return null;
  return { hosts, algorithm, key };
}

export function mergeKnownHostText(existing: string, additions: readonly string[]): string {
  const lines = existing.split("\n").filter((line, index, all) => line.length > 0 || index < all.length - 1);
  const nonEmpty = lines.filter((line) => line.trim().length > 0);
  for (const rawAddition of additions) {
    const addition = rawAddition.trim();
    if (nonEmpty.includes(addition)) continue;
    const incoming = knownHostIdentity(addition);
    if (incoming === null) throw new Error("invalid_host_key");
    for (const line of nonEmpty) {
      const current = knownHostIdentity(line);
      if (current === null || current.algorithm !== incoming.algorithm) continue;
      if (current.hosts.some((host) => incoming.hosts.includes(host)) && current.key !== incoming.key) {
        throw new Error("known_hosts_key_conflict");
      }
    }
    lines.push(addition);
    nonEmpty.push(addition);
  }
  return lines.length === 0 ? "" : `${lines.join("\n").replace(/\n+$/, "")}\n`;
}

export function sshArguments(
  profile: StoredProfile,
  identityFile: string,
  knownHostsFile: string,
  localPort: number,
): string[] {
  const hasControlCharacter = [...knownHostsFile].some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint <= 0x1f || codePoint === 0x7f;
  });
  if (!path.isAbsolute(knownHostsFile) || hasControlCharacter) {
    throw new Error("invalid_known_hosts_path");
  }
  // `ssh -o` reparses each option with OpenSSH config syntax even when spawn
  // receives an argv array. Quote the value inside that option so the normal
  // macOS Application Support path remains one filename, and escape `%` so a
  // literal path segment cannot be interpreted as an OpenSSH token.
  const quotedKnownHostsFile = `"${knownHostsFile
    .replaceAll("\\", "\\\\")
    .replaceAll('"', '\\"')
    .replaceAll("%", "%%")}"`;
  return [
    "-N",
    "-T",
    "-p", String(profile.sshPort),
    "-i", identityFile,
    "-L", `127.0.0.1:${localPort}:127.0.0.1:${profile.gatewayPort}`,
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "IdentityAgent=none",
    "-o", "StrictHostKeyChecking=yes",
    "-o", `UserKnownHostsFile=${quotedKnownHostsFile}`,
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ConnectTimeout=5",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    `${profile.sshUser}@${profile.address}`,
  ];
}

export class DeviceManager {
  private readonly supportDir: string;
  private readonly devicesFile: string;
  private readonly knownHostsFile: string;
  private readonly identityFile: string;
  private readonly sshUser: string;
  private readonly remoteGatewayPort: number;
  private readonly candidates = new Map<string, CandidateRecord>();
  private liveTunnel: LiveTunnel | null = null;

  constructor(options: DeviceManagerOptions) {
    if (!path.isAbsolute(options.supportDir) || options.supportDir.includes("\0")) {
      throw new Error("invalid_support_directory");
    }
    this.supportDir = options.supportDir;
    this.devicesFile = path.join(options.supportDir, "devices.json");
    this.knownHostsFile = path.join(options.supportDir, "known_hosts");
    this.identityFile = options.identityFile ?? path.join(os.homedir(), ".ssh", "id_ed25519_noob_uconsole");
    this.sshUser = options.sshUser ?? "kali";
    this.remoteGatewayPort = validPort(options.remoteGatewayPort ?? 8765);
    if (!path.isAbsolute(this.identityFile) || !SSH_USER_PATTERN.test(this.sshUser)) {
      throw new Error("invalid_device_manager_options");
    }
  }

  get currentDeviceId(): string | null {
    return this.liveTunnel?.deviceId ?? null;
  }

  private async ensureFiles(): Promise<void> {
    await mkdir(this.supportDir, { recursive: true, mode: 0o700 });
    const directoryInfo = await lstat(this.supportDir);
    if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink()) {
      throw new Error("invalid_support_directory");
    }
    await chmod(this.supportDir, 0o700);
    try {
      await access(this.knownHostsFile);
    } catch {
      await writeFile(this.knownHostsFile, "", { encoding: "utf8", mode: 0o600, flag: "wx" });
    }
    const knownHostsInfo = await lstat(this.knownHostsFile);
    if (!knownHostsInfo.isFile() || knownHostsInfo.isSymbolicLink()) {
      throw new Error("known_hosts_not_regular");
    }
    await chmod(this.knownHostsFile, 0o600);
  }

  private async readStoreUnlocked(): Promise<ParsedDeviceStore> {
    try {
      const info = await lstat(this.devicesFile);
      if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o077) !== 0) {
        throw new Error("device_store_permissions_invalid");
      }
      const raw = JSON.parse(await readFile(this.devicesFile, "utf8")) as unknown;
      return parseDeviceStore(raw);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { store: { version: 2, defaultDeviceId: null, devices: [] }, migrated: false };
      }
      throw error;
    }
  }

  private async writeStoreUnlocked(store: DeviceStore): Promise<void> {
    const validated = canonicalStore(store);
    const temporary = `${this.devicesFile}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
    await writeFile(temporary, `${JSON.stringify(validated, null, 2)}\n`, { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    await rename(temporary, this.devicesFile);
  }

  private async loadStore(): Promise<DeviceStore> {
    await this.ensureFiles();
    return await withFileLock(this.devicesFile, async () => {
      const parsed = await this.readStoreUnlocked();
      if (parsed.migrated) await this.writeStoreUnlocked(parsed.store);
      return parsed.store;
    });
  }

  private async mutateStore(
    mutation: (store: DeviceStore) => Promise<DeviceStore> | DeviceStore,
  ): Promise<DeviceStore> {
    await this.ensureFiles();
    return await withFileLock(this.devicesFile, async () => {
      const parsed = await this.readStoreUnlocked();
      const updated = assertStoreInvariants(await mutation(parsed.store));
      await this.writeStoreUnlocked(updated);
      return updated;
    });
  }

  private async mergeKnownHosts(profiles: readonly StoredProfile[]): Promise<void> {
    await this.ensureFiles();
    await withFileLock(this.knownHostsFile, async () => {
      const info = await lstat(this.knownHostsFile);
      if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o077) !== 0) {
        throw new Error("known_hosts_permissions_invalid");
      }
      const existing = await readFile(this.knownHostsFile, "utf8");
      const merged = mergeKnownHostText(existing, profiles.map((profile) => profile.hostKeyLine));
      if (merged === existing) return;
      const temporary = `${this.knownHostsFile}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
      await writeFile(temporary, merged, { mode: 0o600, flag: "wx" });
      await chmod(temporary, 0o600);
      await rename(temporary, this.knownHostsFile);
    });
  }

  async listDevices(): Promise<{ devices: GatewayDeviceProfile[]; currentDeviceId: string | null }> {
    const store = await this.loadStore();
    return {
      devices: store.devices.map(publicProfile),
      currentDeviceId: this.currentDeviceId,
    };
  }

  async discover(timeoutMs: number): Promise<{ candidates: GatewayDeviceCandidate[] }> {
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 5_000) {
      throw new Error("invalid_discovery_timeout");
    }
    const bonjour = new Bonjour();
    const found = new Map<string, CandidateRecord>();
    try {
      const browser = bonjour.find({ type: "noob-kvm", protocol: "tcp" }, (service) => {
        const address = serviceAddress(service);
        if (address === null || service.port < 1 || service.port > 65_535) return;
        const key = `${address}:${service.port}`;
        found.set(key, {
          candidateId: candidateId(),
          instanceName: service.name.slice(0, 128),
          address,
          sshPort: service.port,
          hostKeyFingerprint: null,
          pairingCode: null,
          product: boundedText(service.txt?.product, 128),
          version: boundedText(service.txt?.version, 64),
          capabilities: serviceCapabilities(service),
          expiresAt: expiry(),
          source: "discovery",
          hostKeyLine: null,
        });
      });
      await new Promise((resolve) => setTimeout(resolve, timeoutMs));
      browser.stop();
    } finally {
      bonjour.destroy();
    }
    for (const candidate of found.values()) this.candidates.set(candidate.candidateId, candidate);
    return { candidates: [...found.values()].slice(0, 32).map(this.publicCandidate) };
  }

  private readonly publicCandidate = (candidate: CandidateRecord): GatewayDeviceCandidate => ({
    candidateId: candidate.candidateId,
    instanceName: candidate.instanceName,
    address: candidate.address,
    sshPort: candidate.sshPort,
    hostKeyFingerprint: candidate.hostKeyFingerprint,
    pairingCode: candidate.pairingCode,
    product: candidate.product,
    version: candidate.version,
    capabilities: [...candidate.capabilities],
    expiresAt: candidate.expiresAt,
    source: candidate.source,
  });

  private async scanHostKey(address: string, sshPort: number, timeoutMs = 3_000): Promise<{ line: string; fingerprint: string }> {
    const host = normalizeDeviceAddress(address);
    const port = validPort(sshPort);
    return await new Promise((resolve, reject) => {
      const child = spawn(SSH_KEYSCAN_EXECUTABLE, [
        "-T", String(Math.max(1, Math.ceil(timeoutMs / 1_000))),
        "-p", String(port),
        host,
      ], {
        stdio: ["ignore", "pipe", "ignore"],
        shell: false,
        env: { PATH: CHILD_PROCESS_PATH },
      });
      let output = "";
      let settled = false;
      const finish = (error: Error | null, value?: { line: string; fingerprint: string }) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error !== null) reject(error);
        else if (value !== undefined) resolve(value);
      };
      const timer = setTimeout(() => {
        child.kill("SIGTERM");
        finish(new Error("host_key_probe_timeout"));
      }, timeoutMs);
      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", (chunk: string) => {
        output += chunk;
        if (output.length > MAX_KEYSCAN_BYTES) {
          child.kill("SIGTERM");
          finish(new Error("host_key_probe_too_large"));
        }
      });
      child.once("error", () => finish(new Error("host_key_probe_failed")));
      child.once("close", () => {
        if (settled) return;
        const lines = output.split("\n").map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
        const selected = lines.find((line) => line.includes(" ssh-ed25519 ")) ?? lines[0];
        if (selected === undefined) return finish(new Error("host_key_unavailable"));
        try {
          finish(null, { line: selected, fingerprint: fingerprintForKeyLine(selected) });
        } catch {
          finish(new Error("invalid_host_key"));
        }
      });
    });
  }

  async probe(address: string, sshPort: number, source: GatewayDeviceSource = "manual"): Promise<GatewayDeviceCandidate> {
    const host = normalizeDeviceAddress(address);
    const port = validPort(sshPort);
    const key = await this.scanHostKey(host, port);
    const candidate: CandidateRecord = {
      candidateId: candidateId(),
      instanceName: `N.O.O.B. at ${host}`,
      address: host,
      sshPort: port,
      hostKeyFingerprint: key.fingerprint,
      pairingCode: pairingCodeForFingerprint(key.fingerprint),
      product: "N.O.O.B.",
      version: null,
      capabilities: [],
      expiresAt: expiry(),
      source,
      hostKeyLine: key.line,
    };
    this.candidates.set(candidate.candidateId, candidate);
    return this.publicCandidate(candidate);
  }

  async inspect(candidateIdValue: string): Promise<GatewayDeviceCandidate> {
    if (!CANDIDATE_ID_PATTERN.test(candidateIdValue)) throw new Error("invalid_candidate_id");
    const candidate = this.candidates.get(candidateIdValue);
    if (candidate === undefined || Date.parse(candidate.expiresAt) <= Date.now()) {
      throw new Error("candidate_expired_or_unknown");
    }
    const key = await this.scanHostKey(candidate.address, candidate.sshPort);
    const updated: CandidateRecord = {
      ...candidate,
      hostKeyFingerprint: key.fingerprint,
      pairingCode: pairingCodeForFingerprint(key.fingerprint),
      hostKeyLine: key.line,
      expiresAt: expiry(),
    };
    this.candidates.set(updated.candidateId, updated);
    return this.publicCandidate(updated);
  }

  async pairAndConnect(candidateIdValue: string, expectedFingerprint: string, profileName: string): Promise<DeviceConnection> {
    if (!CANDIDATE_ID_PATTERN.test(candidateIdValue)) throw new Error("invalid_candidate_id");
    if (!FINGERPRINT_PATTERN.test(expectedFingerprint)) throw new Error("invalid_host_key_fingerprint");
    const candidate = this.candidates.get(candidateIdValue);
    if (candidate === undefined || Date.parse(candidate.expiresAt) <= Date.now()) {
      throw new Error("candidate_expired_or_unknown");
    }
    const resolved = candidate.hostKeyFingerprint === null || candidate.hostKeyLine === null
      ? await this.inspect(candidateIdValue)
      : candidate;
    const record = this.candidates.get(resolved.candidateId);
    if (
      record === undefined || record.hostKeyFingerprint !== expectedFingerprint ||
      record.hostKeyLine === null || fingerprintForKeyLine(record.hostKeyLine) !== expectedFingerprint
    ) throw new Error("host_key_fingerprint_mismatch");

    const deviceId = stableDeviceId(expectedFingerprint);
    const verifiedHostKeyLine = record.hostKeyLine;
    const updatedStore = await this.mutateStore(async (store) => {
      const endpointConflict = store.devices.find((entry) =>
        entry.deviceId !== deviceId &&
        entry.address.toLowerCase() === record.address.toLowerCase() &&
        entry.sshPort === record.sshPort
      );
      if (endpointConflict !== undefined) throw new Error("device_identity_conflict");
      const existingIndex = store.devices.findIndex((entry) => entry.deviceId === deviceId);
      let nextProfile: StoredProfile;
      if (existingIndex < 0) {
        nextProfile = {
          deviceId,
          profileName: validProfileName(profileName),
          address: record.address,
          sshPort: record.sshPort,
          hostKeyFingerprint: expectedFingerprint,
          hostKeyLine: verifiedHostKeyLine,
          sshUser: this.sshUser,
          gatewayPort: this.remoteGatewayPort,
          capabilities: [...record.capabilities],
          createdAt: new Date().toISOString(),
        };
        store.devices.push(nextProfile);
      } else {
        const existing = store.devices[existingIndex];
        if (existing === undefined || existing.hostKeyFingerprint !== expectedFingerprint) {
          throw new Error("device_identity_conflict");
        }
        nextProfile = {
          ...existing,
          profileName: validProfileName(profileName),
          address: record.address,
          sshPort: record.sshPort,
          hostKeyLine: verifiedHostKeyLine,
          capabilities: record.capabilities.length > 0
            ? [...record.capabilities]
            : [...existing.capabilities],
        };
        store.devices[existingIndex] = nextProfile;
      }
      store.defaultDeviceId = deviceId;
      await this.mergeKnownHosts([nextProfile]);
      return store;
    });
    const profile = updatedStore.devices.find((entry) => entry.deviceId === deviceId);
    if (profile === undefined) throw new Error("invalid_device_store");
    return await this.connectProfile(profile);
  }

  async connectKnown(deviceId: string): Promise<DeviceConnection> {
    if (!DEVICE_ID_PATTERN.test(deviceId)) throw new Error("invalid_device_id");
    const updatedStore = await this.mutateStore(async (store) => {
      const selected = store.devices.find((entry) => entry.deviceId === deviceId);
      if (selected === undefined) throw new Error("device_not_registered");
      store.defaultDeviceId = deviceId;
      await this.mergeKnownHosts([selected]);
      return store;
    });
    const profile = updatedStore.devices.find((entry) => entry.deviceId === deviceId);
    if (profile === undefined) throw new Error("device_not_registered");
    return await this.connectProfile(profile);
  }

  async connectDefault(): Promise<DeviceConnection | null> {
    const store = await this.loadStore();
    if (store.defaultDeviceId === null) return null;
    const profile = store.devices.find((entry) => entry.deviceId === store.defaultDeviceId);
    if (profile === undefined) throw new Error("default_device_not_registered");
    await this.mergeKnownHosts([profile]);
    try {
      return await this.connectProfile(profile);
    } catch (initialError) {
      const discovered = await this.discover(1_500);
      for (const candidate of discovered.candidates) {
        let inspected: GatewayDeviceCandidate;
        try {
          inspected = await this.inspect(candidate.candidateId);
        } catch {
          continue;
        }
        if (
          inspected.hostKeyFingerprint !== profile.hostKeyFingerprint ||
          inspected.hostKeyFingerprint === null
        ) continue;
        const record = this.candidates.get(inspected.candidateId);
        if (record?.hostKeyLine === null || record === undefined) continue;
        const verifiedDiscoveredHostKeyLine = record.hostKeyLine;
        const refreshedStore = await this.mutateStore(async (current) => {
          const index = current.devices.findIndex((entry) => entry.deviceId === profile.deviceId);
          const pinned = index >= 0 ? current.devices[index] : undefined;
          if (pinned === undefined || pinned.hostKeyFingerprint !== inspected.hostKeyFingerprint) {
            throw new Error("device_identity_conflict");
          }
          const endpointConflict = current.devices.find((entry) =>
            entry.deviceId !== pinned.deviceId &&
            entry.address.toLowerCase() === inspected.address.toLowerCase() &&
            entry.sshPort === inspected.sshPort
          );
          if (endpointConflict !== undefined) throw new Error("device_identity_conflict");
          const refreshed: StoredProfile = {
            ...pinned,
            address: inspected.address,
            sshPort: inspected.sshPort,
            hostKeyLine: verifiedDiscoveredHostKeyLine,
            capabilities: inspected.capabilities.length > 0
              ? [...inspected.capabilities]
              : [...pinned.capabilities],
          };
          current.devices[index] = refreshed;
          current.defaultDeviceId = refreshed.deviceId;
          await this.mergeKnownHosts([refreshed]);
          return current;
        });
        const refreshed = refreshedStore.devices.find((entry) => entry.deviceId === profile.deviceId);
        if (refreshed !== undefined) return await this.connectProfile(refreshed);
      }
      throw initialError;
    }
  }

  private async connectProfile(profile: StoredProfile): Promise<DeviceConnection> {
    await ownerOnlyRegularFile(this.identityFile);
    await this.close();
    const localPort = await freeLoopbackPort();
    const gatewayUrl = `http://127.0.0.1:${localPort}`;
    const child = spawn(SSH_EXECUTABLE, sshArguments(profile, this.identityFile, this.knownHostsFile, localPort), {
      stdio: ["ignore", "ignore", "ignore"],
      shell: false,
      env: { PATH: CHILD_PROCESS_PATH },
    });
    const tunnel: LiveTunnel = { child, deviceId: profile.deviceId, gatewayUrl };
    this.liveTunnel = tunnel;
    child.once("exit", () => {
      if (this.liveTunnel === tunnel) this.liveTunnel = null;
    });
    try {
      await waitForGateway(gatewayUrl, child);
      return { device: publicProfile(profile), gatewayUrl };
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  async close(): Promise<void> {
    const tunnel = this.liveTunnel;
    this.liveTunnel = null;
    if (tunnel === null || tunnel.child.exitCode !== null) return;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        if (tunnel.child.exitCode === null) tunnel.child.kill("SIGKILL");
      }, 1_000);
      tunnel.child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      tunnel.child.kill("SIGTERM");
    });
  }
}
