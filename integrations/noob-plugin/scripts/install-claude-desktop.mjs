#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const EXPECTED_PACKAGE_NAME = "noob-mcp-server";
const MAX_CONFIG_BYTES = 1024 * 1024;
const REQUIRED_MODE = 0o600;
const SEMVER_PATTERN = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

export class ClaudeDesktopInstallError extends Error {
  constructor(code, safeMessage) {
    super(safeMessage);
    this.name = "ClaudeDesktopInstallError";
    this.code = code;
    this.safeMessage = safeMessage;
  }
}

function fail(code, safeMessage) {
  throw new ClaudeDesktopInstallError(code, safeMessage);
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeRealpath(candidate, code, message) {
  try {
    return fs.realpathSync(candidate);
  } catch {
    fail(code, message);
  }
}

function safeLstat(candidate, code, message) {
  try {
    return fs.lstatSync(candidate);
  } catch {
    fail(code, message);
  }
}

function readJsonFile(candidate, code, message) {
  const metadata = safeLstat(candidate, code, message);
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    fail(code, message);
  }

  try {
    return JSON.parse(fs.readFileSync(candidate, "utf8"));
  } catch {
    fail(code, message);
  }
}

export function validateConfigMetadata(metadata, expectedUid) {
  if (metadata.isSymbolicLink()) {
    fail("config_symlink", "Claude Desktop configuration must not be a symbolic link.");
  }
  if (!metadata.isFile()) {
    fail("config_not_regular", "Claude Desktop configuration must be a regular file.");
  }
  if (metadata.uid !== expectedUid) {
    fail("config_wrong_owner", "Claude Desktop configuration must be owned by the current user.");
  }
  if ((metadata.mode & 0o777) !== REQUIRED_MODE) {
    fail("config_permissions", "Claude Desktop configuration must have mode 0600.");
  }
  if (metadata.size > MAX_CONFIG_BYTES) {
    fail("config_too_large", "Claude Desktop configuration exceeds the bounded installer limit.");
  }
}

function sameIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function sameSnapshot(left, right) {
  return (
    sameIdentity(left, right) &&
    left.size === right.size &&
    left.mtimeMs === right.mtimeMs &&
    left.ctimeMs === right.ctimeMs
  );
}

function verifyUnchangedConfig(configPath, initialMetadata, expectedUid) {
  const currentMetadata = safeLstat(
    configPath,
    "config_changed",
    "Claude Desktop configuration changed during installation; no replacement was attempted.",
  );
  validateConfigMetadata(currentMetadata, expectedUid);
  if (!sameSnapshot(initialMetadata, currentMetadata)) {
    fail(
      "config_changed",
      "Claude Desktop configuration changed during installation; no replacement was attempted.",
    );
  }
}

function parseOwnerConfig(raw) {
  let config;
  try {
    config = JSON.parse(raw);
  } catch {
    fail("config_invalid_json", "Claude Desktop configuration is not valid JSON.");
  }

  if (!isRecord(config)) {
    fail("config_invalid_root", "Claude Desktop configuration root must be a JSON object.");
  }
  if (config.mcpServers !== undefined && !isRecord(config.mcpServers)) {
    fail("config_invalid_mcp_servers", "Claude Desktop mcpServers must be a JSON object.");
  }
  return config;
}

function readOwnerConfig(configPath, expectedUid) {
  let initialMetadata;
  try {
    initialMetadata = fs.lstatSync(configPath);
  } catch (error) {
    if (error?.code === "ENOENT") {
      fail("config_missing", "Claude Desktop configuration does not exist.");
    }
    fail("config_unreadable", "Claude Desktop configuration could not be inspected.");
  }
  validateConfigMetadata(initialMetadata, expectedUid);

  const noFollow = fs.constants.O_NOFOLLOW ?? 0;
  let descriptor;
  try {
    descriptor = fs.openSync(configPath, fs.constants.O_RDONLY | noFollow);
  } catch {
    fail("config_unreadable", "Claude Desktop configuration could not be opened safely.");
  }

  try {
    const openedMetadata = fs.fstatSync(descriptor);
    validateConfigMetadata(openedMetadata, expectedUid);
    if (!sameSnapshot(initialMetadata, openedMetadata)) {
      fail(
        "config_changed",
        "Claude Desktop configuration changed during installation; no replacement was attempted.",
      );
    }
    return {
      initialMetadata,
      raw: fs.readFileSync(descriptor, "utf8"),
    };
  } catch (error) {
    if (error instanceof ClaudeDesktopInstallError) {
      throw error;
    }
    fail("config_unreadable", "Claude Desktop configuration could not be read safely.");
  } finally {
    fs.closeSync(descriptor);
  }
}

function validateRegularCanonicalFile(candidate, code, message) {
  const metadata = safeLstat(candidate, code, message);
  if (metadata.isSymbolicLink() || !metadata.isFile()) {
    fail(code, message);
  }
  if (safeRealpath(candidate, code, message) !== candidate) {
    fail(code, message);
  }
  return metadata;
}

export function prepareServerDefinition({ pluginRoot, nodeExecutable }) {
  const canonicalPluginRoot = safeRealpath(
    pluginRoot,
    "plugin_root_invalid",
    "Prepared N.O.O.B. plugin root could not be resolved.",
  );
  const rootMetadata = safeLstat(
    canonicalPluginRoot,
    "plugin_root_invalid",
    "Prepared N.O.O.B. plugin root could not be inspected.",
  );
  if (!rootMetadata.isDirectory()) {
    fail("plugin_root_invalid", "Prepared N.O.O.B. plugin root must be a directory.");
  }

  const canonicalNode = safeRealpath(
    nodeExecutable,
    "node_invalid",
    "Current Node.js executable could not be resolved.",
  );
  const nodeMetadata = validateRegularCanonicalFile(
    canonicalNode,
    "node_invalid",
    "Current Node.js executable must resolve to a regular file.",
  );
  if ((nodeMetadata.mode & 0o111) === 0) {
    fail("node_invalid", "Current Node.js executable is not executable.");
  }

  const packagePath = path.join(canonicalPluginRoot, "package.json");
  const packageJson = readJsonFile(
    packagePath,
    "package_invalid",
    "Prepared N.O.O.B. package metadata is missing or invalid.",
  );
  if (
    !isRecord(packageJson) ||
    packageJson.name !== EXPECTED_PACKAGE_NAME ||
    packageJson.main !== "dist/main.js" ||
    typeof packageJson.version !== "string" ||
    !SEMVER_PATTERN.test(packageJson.version)
  ) {
    fail("package_invalid", "Prepared N.O.O.B. package metadata is inconsistent.");
  }

  const claudeManifest = readJsonFile(
    path.join(canonicalPluginRoot, ".claude-plugin", "plugin.json"),
    "package_version_mismatch",
    "Claude plugin manifest is missing or has an inconsistent package version.",
  );
  const bundleManifest = readJsonFile(
    path.join(canonicalPluginRoot, "mcpb", "manifest.json"),
    "package_version_mismatch",
    "Claude bundle manifest is missing or has an inconsistent package version.",
  );
  if (
    !isRecord(claudeManifest) ||
    claudeManifest.name !== "noob-plugin" ||
    claudeManifest.version !== packageJson.version ||
    !isRecord(bundleManifest) ||
    bundleManifest.version !== packageJson.version
  ) {
    fail(
      "package_version_mismatch",
      "Claude plugin manifests do not match the prepared N.O.O.B. package version.",
    );
  }

  const entrypoint = path.join(canonicalPluginRoot, "dist", "main.js");
  const entryMetadata = validateRegularCanonicalFile(
    entrypoint,
    "entrypoint_invalid",
    "Prepared N.O.O.B. dist/main.js is missing or unsafe.",
  );
  if (entryMetadata.size === 0) {
    fail("entrypoint_invalid", "Prepared N.O.O.B. dist/main.js is empty.");
  }

  return {
    version: packageJson.version,
    server: {
      command: canonicalNode,
      args: [entrypoint, "--stdio"],
      cwd: canonicalPluginRoot,
    },
  };
}

function sameServerDefinition(existing, expected) {
  if (!isRecord(existing)) {
    return false;
  }
  const keys = Object.keys(existing).sort();
  if (keys.length !== 3 || keys[0] !== "args" || keys[1] !== "command" || keys[2] !== "cwd") {
    return false;
  }
  return (
    existing.command === expected.command &&
    existing.cwd === expected.cwd &&
    Array.isArray(existing.args) &&
    existing.args.length === 2 &&
    existing.args[0] === expected.args[0] &&
    existing.args[1] === "--stdio"
  );
}

function formatTimestamp(date) {
  return date.toISOString().replace(/[-:.]/g, "");
}

function createExclusiveFile(directory, filenameFactory, content) {
  const noFollow = fs.constants.O_NOFOLLOW ?? 0;
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const candidate = path.join(directory, filenameFactory(attempt));
    let descriptor;
    try {
      descriptor = fs.openSync(
        candidate,
        fs.constants.O_WRONLY |
          fs.constants.O_CREAT |
          fs.constants.O_EXCL |
          noFollow,
        REQUIRED_MODE,
      );
    } catch (error) {
      if (error?.code === "EEXIST") {
        continue;
      }
      fail("write_failed", "Protected installer output could not be created.");
    }

    try {
      fs.fchmodSync(descriptor, REQUIRED_MODE);
      fs.writeFileSync(descriptor, content);
      fs.fsyncSync(descriptor);
    } catch {
      try {
        fs.closeSync(descriptor);
      } finally {
        try {
          fs.unlinkSync(candidate);
        } catch {
          // Best-effort cleanup of only the unique file created by this process.
        }
      }
      fail("write_failed", "Protected installer output could not be written.");
    }
    fs.closeSync(descriptor);
    return candidate;
  }
  fail("write_failed", "A unique protected installer output name could not be allocated.");
}

function fsyncDirectory(directory) {
  let descriptor;
  try {
    descriptor = fs.openSync(directory, fs.constants.O_RDONLY);
    fs.fsyncSync(descriptor);
  } catch {
    fail("write_failed", "Claude Desktop configuration directory could not be synchronized.");
  } finally {
    if (descriptor !== undefined) {
      fs.closeSync(descriptor);
    }
  }
}

function safeUid() {
  if (typeof process.getuid !== "function") {
    fail("unsupported_platform", "This installer requires a POSIX owner-aware filesystem.");
  }
  return process.getuid();
}

export function installClaudeDesktop({
  configPath,
  pluginRoot,
  nodeExecutable = process.execPath,
  dryRun = false,
  now = () => new Date(),
  randomSuffix = () => crypto.randomBytes(6).toString("hex"),
  hooks = {},
}) {
  if (!path.isAbsolute(configPath)) {
    fail("config_path_relative", "Claude Desktop configuration path must be absolute.");
  }

  const expectedUid = safeUid();
  const prepared = prepareServerDefinition({ pluginRoot, nodeExecutable });
  const { initialMetadata, raw } = readOwnerConfig(configPath, expectedUid);
  const config = parseOwnerConfig(raw);
  const existingNoob = config.mcpServers?.noob;

  if (existingNoob !== undefined) {
    if (!sameServerDefinition(existingNoob, prepared.server)) {
      fail(
        "noob_conflict",
        "Claude Desktop already contains a different mcpServers.noob entry.",
      );
    }
    return { status: "already-configured", version: prepared.version, backupCreated: false };
  }

  if (dryRun) {
    return { status: "dry-run", version: prepared.version, backupCreated: false };
  }

  const merged = {
    ...config,
    mcpServers: {
      ...(config.mcpServers ?? {}),
      noob: prepared.server,
    },
  };
  const replacement = `${JSON.stringify(merged, null, 2)}\n`;
  const directory = path.dirname(configPath);
  const basename = path.basename(configPath);

  verifyUnchangedConfig(configPath, initialMetadata, expectedUid);
  const stamp = formatTimestamp(now());
  createExclusiveFile(
    directory,
    (attempt) => `${basename}.backup-${stamp}-${randomSuffix()}-${attempt}`,
    raw,
  );
  fsyncDirectory(directory);

  let temporaryPath;
  try {
    temporaryPath = createExclusiveFile(
      directory,
      (attempt) => `.${basename}.noob-install-${process.pid}-${randomSuffix()}-${attempt}.tmp`,
      replacement,
    );
    hooks.beforeFinalValidation?.();
    verifyUnchangedConfig(configPath, initialMetadata, expectedUid);
    fs.renameSync(temporaryPath, configPath);
    temporaryPath = undefined;
    fs.chmodSync(configPath, REQUIRED_MODE);
    fsyncDirectory(directory);
  } catch (error) {
    if (temporaryPath !== undefined) {
      try {
        fs.unlinkSync(temporaryPath);
      } catch {
        // Best-effort cleanup of only this invocation's uniquely named temporary file.
      }
    }
    if (error instanceof ClaudeDesktopInstallError) {
      throw error;
    }
    fail("write_failed", "Claude Desktop configuration could not be replaced atomically.");
  }

  return { status: "installed", version: prepared.version, backupCreated: true };
}

export function formatInstallStatus(result) {
  if (result.status === "dry-run") {
    return `OK [dry-run]: N.O.O.B. ${result.version} is ready; no files changed.`;
  }
  if (result.status === "already-configured") {
    return `OK [already-configured]: N.O.O.B. ${result.version} already matches; no files changed.`;
  }
  return `OK [installed]: N.O.O.B. ${result.version} installed; protected backup created.`;
}

function parseArguments(argv) {
  const defaultConfig = path.join(
    os.homedir(),
    "Library",
    "Application Support",
    "Claude",
    "claude_desktop_config.json",
  );
  const options = { configPath: defaultConfig, dryRun: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (argument === "--config") {
      const value = argv[index + 1];
      if (!value) {
        fail("arguments_invalid", "--config requires an absolute path.");
      }
      options.configPath = value;
      index += 1;
      continue;
    }
    fail("arguments_invalid", "Supported options are --dry-run and --config ABSOLUTE_PATH.");
  }
  return options;
}

function isMainModule() {
  if (!process.argv[1]) {
    return false;
  }
  try {
    return fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMainModule()) {
  try {
    const options = parseArguments(process.argv.slice(2));
    const scriptDirectory = path.dirname(fs.realpathSync(fileURLToPath(import.meta.url)));
    const result = installClaudeDesktop({
      ...options,
      pluginRoot: path.resolve(scriptDirectory, ".."),
      nodeExecutable: process.execPath,
    });
    process.stdout.write(`${formatInstallStatus(result)}\n`);
  } catch (error) {
    if (error instanceof ClaudeDesktopInstallError) {
      process.stderr.write(`ERROR [${error.code}]: ${error.safeMessage}\n`);
    } else {
      process.stderr.write("ERROR [internal_error]: N.O.O.B. installer could not complete safely.\n");
    }
    process.exitCode = 1;
  }
}
