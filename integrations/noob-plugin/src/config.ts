import { randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { access, chmod, lstat, mkdir, readFile, rename, rmdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { z } from "zod";
import { fingerprintForKeyLine, stableDeviceId } from "./discovery.js";
import { assertAbsolutePath, assertAddress } from "./policy.js";
import type { DeviceProfile, DeviceStore } from "./types.js";

const FILE_LOCK_RETRY_MS = 20;
const FILE_LOCK_ATTEMPTS = 250;
const STALE_FILE_LOCK_MS = 30_000;

const profileSchema = z.object({
  device_id: z.string().regex(/^noob_[a-z0-9]{16,64}$/),
  profile_name: z.string().min(1).max(64),
  address: z.string().min(1).max(253),
  ssh_port: z.number().int().min(1).max(65535),
  ssh_user: z.string().regex(/^[a-z_][a-z0-9_-]{0,31}$/i),
  host_key_sha256: z.string().regex(/^SHA256:[A-Za-z0-9+/]{43}=?$/),
  host_key_line: z.string().min(32).max(4096),
  gateway_port: z.number().int().min(1).max(65535),
  capabilities: z.array(z.string().regex(/^[a-z0-9-]{1,48}$/)).max(32),
  created_at: z.string().datetime(),
}).strict();

const storeSchema = z.object({
  version: z.literal(2),
  default_device_id: z.string().regex(/^noob_[a-z0-9]{16,64}$/).nullable(),
  devices: z.array(profileSchema).max(64),
}).strict();

const pluginV1StoreSchema = z.object({
  version: z.literal(1),
  default_device_id: z.string().regex(/^noob_[a-z0-9]{16,64}$/).nullable(),
  devices: z.array(profileSchema).max(64),
}).strict();

const electronV1ProfileSchema = z.object({
  deviceId: z.string().regex(/^noob_[a-z0-9]{16,64}$/),
  profileName: z.string().min(1).max(64),
  address: z.string().min(1).max(253),
  sshPort: z.number().int().min(1).max(65535),
  sshUser: z.string().regex(/^[a-z_][a-z0-9_-]{0,31}$/i),
  hostKeyFingerprint: z.string().regex(/^SHA256:[A-Za-z0-9+/]{43}$/),
  hostKeyLine: z.string().min(32).max(4096),
  gatewayPort: z.number().int().min(1).max(65535),
  capabilities: z.array(z.string().regex(/^[a-z0-9-]{1,48}$/)).max(32),
  createdAt: z.string().datetime(),
}).strict();

const electronV1StoreSchema = z.object({
  version: z.literal(1),
  devices: z.array(electronV1ProfileSchema).max(64),
}).strict();

export interface RuntimePaths {
  support_dir: string;
  devices_file: string;
  known_hosts_file: string;
  identity_file: string;
  gateway_token_file: string;
}

function defaultSupportDir(): string {
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "N.O.O.B");
  }
  return path.join(process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config"), "noob");
}

export function runtimePaths(): RuntimePaths {
  const support_dir = process.env.NOOB_PLUGIN_SUPPORT_DIR?.trim() || defaultSupportDir();
  const devices_file = process.env.NOOB_PLUGIN_CONFIG?.trim() || path.join(support_dir, "devices.json");
  const identity_file = process.env.NOOB_SSH_IDENTITY_FILE?.trim() || path.join(os.homedir(), ".ssh", "id_ed25519_noob_uconsole");
  const gateway_token_file = process.env.NOOB_GATEWAY_TOKEN_FILE?.trim() || path.join(support_dir, "gateway.token");
  const known_hosts_file = process.env.NOOB_KNOWN_HOSTS_FILE?.trim() || path.join(support_dir, "known_hosts");
  for (const [label, value] of Object.entries({ support_dir, devices_file, identity_file, gateway_token_file, known_hosts_file })) {
    assertAbsolutePath(value, label);
  }
  return {
    support_dir,
    devices_file,
    known_hosts_file,
    identity_file,
    gateway_token_file,
  };
}

async function assertPrivateFile(file: string): Promise<void> {
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink()) throw new Error("protected_file_not_regular");
  if ((info.mode & 0o077) !== 0) throw new Error("protected_file_permissions_too_open");
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
        const info = await lstat(lockDirectory);
        if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("device_store_lock_invalid");
        if (Date.now() - info.mtimeMs > STALE_FILE_LOCK_MS) {
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

function validateStore(store: DeviceStore): DeviceStore {
  const validated = storeSchema.parse(store) as DeviceStore;
  const deviceIds = new Set<string>();
  const fingerprints = new Set<string>();
  const endpoints = new Map<string, string>();
  for (const profile of validated.devices) {
    assertAddress(profile.address);
    if (
      stableDeviceId(profile.host_key_sha256) !== profile.device_id ||
      fingerprintForKeyLine(profile.host_key_line) !== profile.host_key_sha256
    ) {
      throw new Error("invalid_device_store");
    }
    if (deviceIds.has(profile.device_id) || fingerprints.has(profile.host_key_sha256)) {
      throw new Error("device_store_conflict");
    }
    deviceIds.add(profile.device_id);
    fingerprints.add(profile.host_key_sha256);
    const endpoint = `${profile.address.toLowerCase()}\0${profile.ssh_port}`;
    const pinned = endpoints.get(endpoint);
    if (pinned !== undefined && pinned !== profile.host_key_sha256) {
      throw new Error("device_store_conflict");
    }
    endpoints.set(endpoint, profile.host_key_sha256);
  }
  if (validated.default_device_id !== null && !deviceIds.has(validated.default_device_id)) {
    throw new Error("invalid_device_store");
  }
  return validated;
}

function normalizeStore(raw: unknown): { store: DeviceStore; migrated: boolean } {
  const current = storeSchema.safeParse(raw);
  if (current.success) return { store: validateStore(current.data as DeviceStore), migrated: false };

  const pluginV1 = pluginV1StoreSchema.safeParse(raw);
  if (pluginV1.success) {
    return {
      store: validateStore({
        version: 2,
        default_device_id: pluginV1.data.default_device_id,
        devices: pluginV1.data.devices as DeviceProfile[],
      }),
      migrated: true,
    };
  }

  const electronV1 = electronV1StoreSchema.safeParse(raw);
  if (electronV1.success) {
    const devices: DeviceProfile[] = electronV1.data.devices.map((profile) => ({
      device_id: profile.deviceId,
      profile_name: profile.profileName,
      address: profile.address,
      ssh_port: profile.sshPort,
      ssh_user: profile.sshUser,
      host_key_sha256: profile.hostKeyFingerprint,
      host_key_line: profile.hostKeyLine,
      gateway_port: profile.gatewayPort,
      capabilities: [...new Set(profile.capabilities)],
      created_at: profile.createdAt,
    }));
    return {
      store: validateStore({
        version: 2,
        default_device_id: devices.length === 1 ? devices[0]?.device_id ?? null : null,
        devices,
      }),
      migrated: true,
    };
  }

  const version = typeof raw === "object" && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>).version
    : undefined;
  if (typeof version === "number" && version !== 1 && version !== 2) {
    throw new Error("unsupported_device_store_version");
  }
  throw new Error("invalid_device_store");
}

async function writeStoreUnlocked(file: string, store: DeviceStore): Promise<void> {
  const validated = validateStore(store);
  const temporary = `${file}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
  await writeFile(temporary, `${JSON.stringify(validated, null, 2)}\n`, { mode: 0o600, flag: "wx" });
  await chmod(temporary, 0o600);
  await rename(temporary, file);
}

async function readStoreUnlocked(file: string): Promise<{ store: DeviceStore; migrated: boolean }> {
  try {
    await assertPrivateFile(file);
    return normalizeStore(JSON.parse(await readFile(file, "utf8")) as unknown);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { store: { version: 2, default_device_id: null, devices: [] }, migrated: false };
    }
    throw error;
  }
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

export async function readProtectedText(file: string, min = 1, max = 4096): Promise<string> {
  await assertPrivateFile(file);
  const value = await readFile(file, "utf8");
  if (value.length < min || value.length > max || value.includes("\0")) {
    throw new Error("protected_file_invalid");
  }
  return value.trim();
}

export async function ensureRuntimeFiles(): Promise<RuntimePaths> {
  const paths = runtimePaths();
  await mkdir(paths.support_dir, { recursive: true, mode: 0o700 });
  const supportInfo = await lstat(paths.support_dir);
  if (!supportInfo.isDirectory() || supportInfo.isSymbolicLink()) {
    throw new Error("invalid_support_directory");
  }
  await chmod(paths.support_dir, 0o700);
  try {
    await access(paths.known_hosts_file, constants.F_OK);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    try {
      await writeFile(paths.known_hosts_file, "", { mode: 0o600, flag: "wx" });
    } catch (creationError) {
      if ((creationError as NodeJS.ErrnoException).code !== "EEXIST") throw creationError;
    }
  }
  const knownHostsInfo = await lstat(paths.known_hosts_file);
  if (!knownHostsInfo.isFile() || knownHostsInfo.isSymbolicLink()) {
    throw new Error("known_hosts_not_regular");
  }
  if ((knownHostsInfo.mode & 0o077) !== 0) throw new Error("known_hosts_permissions_too_open");
  return paths;
}

export async function loadStore(): Promise<DeviceStore> {
  const paths = await ensureRuntimeFiles();
  return await withFileLock(paths.devices_file, async () => {
    const parsed = await readStoreUnlocked(paths.devices_file);
    if (parsed.migrated) await writeStoreUnlocked(paths.devices_file, parsed.store);
    return parsed.store;
  });
}

export async function mutateStore(
  mutation: (store: DeviceStore) => DeviceStore | Promise<DeviceStore>,
): Promise<DeviceStore> {
  const paths = await ensureRuntimeFiles();
  return await withFileLock(paths.devices_file, async () => {
    const parsed = await readStoreUnlocked(paths.devices_file);
    const updated = validateStore(await mutation(parsed.store));
    await writeStoreUnlocked(paths.devices_file, updated);
    return updated;
  });
}

export async function addKnownHost(profile: DeviceProfile): Promise<void> {
  const paths = await ensureRuntimeFiles();
  if (
    stableDeviceId(profile.host_key_sha256) !== profile.device_id ||
    fingerprintForKeyLine(profile.host_key_line) !== profile.host_key_sha256
  ) {
    throw new Error("invalid_host_key");
  }
  await withFileLock(paths.known_hosts_file, async () => {
    await assertPrivateFile(paths.known_hosts_file);
    const existing = await readFile(paths.known_hosts_file, "utf8");
    const merged = mergeKnownHostText(existing, [profile.host_key_line]);
    if (merged === existing) return;
    const temporary = `${paths.known_hosts_file}.tmp-${process.pid}-${randomBytes(6).toString("hex")}`;
    await writeFile(temporary, merged, { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    await rename(temporary, paths.known_hosts_file);
  });
}
