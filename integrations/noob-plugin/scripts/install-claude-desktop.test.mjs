import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  ClaudeDesktopInstallError,
  formatInstallStatus,
  installClaudeDesktop,
  prepareServerDefinition,
  validateConfigMetadata,
} from "./install-claude-desktop.mjs";

const temporaryRoots = [];

function makeTemporaryRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "noob-claude-installer-"));
  temporaryRoots.push(root);
  return root;
}

function writeJson(candidate, value, mode = 0o600) {
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  fs.writeFileSync(candidate, `${JSON.stringify(value, null, 2)}\n`, { mode });
  fs.chmodSync(candidate, mode);
}

function makePreparedPlugin(root, version = "0.2.0") {
  const pluginRoot = path.join(root, "prepared-plugin");
  fs.mkdirSync(path.join(pluginRoot, "dist"), { recursive: true });
  fs.mkdirSync(path.join(pluginRoot, ".claude-plugin"), { recursive: true });
  fs.mkdirSync(path.join(pluginRoot, "mcpb"), { recursive: true });
  writeJson(path.join(pluginRoot, "package.json"), {
    name: "noob-mcp-server",
    version,
    main: "dist/main.js",
  });
  writeJson(path.join(pluginRoot, ".claude-plugin", "plugin.json"), {
    name: "noob-plugin",
    version,
  });
  writeJson(path.join(pluginRoot, "mcpb", "manifest.json"), { version });
  fs.writeFileSync(path.join(pluginRoot, "dist", "main.js"), "console.log('prepared');\n");
  return pluginRoot;
}

function makeOwnerConfig(root, value, mode = 0o600) {
  const configPath = path.join(root, "Claude", "claude_desktop_config.json");
  writeJson(configPath, value, mode);
  return configPath;
}

function installOptions(root, configPath, overrides = {}) {
  return {
    configPath,
    pluginRoot: makePreparedPlugin(root),
    nodeExecutable: process.execPath,
    now: () => new Date("2026-08-28T04:05:06.789Z"),
    randomSuffix: (() => {
      let sequence = 0;
      return () => `fixed${sequence++}`;
    })(),
    ...overrides,
  };
}

function expectInstallError(action, code) {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(ClaudeDesktopInstallError);
    expect(error.code).toBe(code);
    return;
  }
  throw new Error(`Expected ClaudeDesktopInstallError ${code}.`);
}

function backupFiles(configPath) {
  const prefix = `${path.basename(configPath)}.backup-`;
  return fs
    .readdirSync(path.dirname(configPath))
    .filter((entry) => entry.startsWith(prefix))
    .sort();
}

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe("Claude Desktop installer", () => {
  it("dry-runs without changing the config or creating installer files", () => {
    const root = makeTemporaryRoot();
    const original = {
      theme: "dark",
      nested: { preserved: [1, "two", { three: true }] },
      mcpServers: { existing: { command: "/usr/bin/true", args: [] } },
    };
    const configPath = makeOwnerConfig(root, original);
    const before = fs.readFileSync(configPath);
    const beforeEntries = fs.readdirSync(path.dirname(configPath)).sort();

    const result = installClaudeDesktop({
      ...installOptions(root, configPath),
      dryRun: true,
    });

    expect(result).toEqual({ status: "dry-run", version: "0.2.0", backupCreated: false });
    expect(fs.readFileSync(configPath)).toEqual(before);
    expect(fs.readdirSync(path.dirname(configPath)).sort()).toEqual(beforeEntries);
    expect(formatInstallStatus(result)).toBe(
      "OK [dry-run]: N.O.O.B. 0.2.0 is ready; no files changed.",
    );
  });

  it("atomically merges only mcpServers.noob and creates an exact protected backup", () => {
    const root = makeTemporaryRoot();
    const original = {
      theme: "dark",
      feature: { enabled: true, threshold: 7 },
      mcpServers: { existing: { command: "/usr/bin/true", args: ["--safe"] } },
    };
    const configPath = makeOwnerConfig(root, original);
    const originalBytes = fs.readFileSync(configPath);
    const options = installOptions(root, configPath);
    const expected = prepareServerDefinition(options);

    const result = installClaudeDesktop(options);

    expect(result).toEqual({ status: "installed", version: "0.2.0", backupCreated: true });
    const installed = JSON.parse(fs.readFileSync(configPath, "utf8"));
    expect(installed.theme).toBe(original.theme);
    expect(installed.feature).toEqual(original.feature);
    expect(installed.mcpServers.existing).toEqual(original.mcpServers.existing);
    expect(installed.mcpServers.noob).toEqual(expected.server);
    expect(path.isAbsolute(installed.mcpServers.noob.command)).toBe(true);
    expect(path.isAbsolute(installed.mcpServers.noob.args[0])).toBe(true);
    expect(installed.mcpServers.noob.args).toEqual([
      path.join(fs.realpathSync(options.pluginRoot), "dist", "main.js"),
      "--stdio",
    ]);
    expect(installed.mcpServers.noob.cwd).toBe(fs.realpathSync(options.pluginRoot));
    expect(fs.statSync(configPath).mode & 0o777).toBe(0o600);

    const backups = backupFiles(configPath);
    expect(backups).toHaveLength(1);
    expect(backups[0]).toMatch(
      /^claude_desktop_config\.json\.backup-20260828T040506789Z-fixed0-0$/,
    );
    const backupPath = path.join(path.dirname(configPath), backups[0]);
    expect(fs.readFileSync(backupPath)).toEqual(originalBytes);
    expect(fs.statSync(backupPath).mode & 0o777).toBe(0o600);
  });

  it("is idempotent when the exact generated noob entry already exists", () => {
    const root = makeTemporaryRoot();
    const configPath = makeOwnerConfig(root, { mcpServers: {} });
    const options = installOptions(root, configPath);
    installClaudeDesktop(options);
    const afterFirstInstall = fs.readFileSync(configPath);
    const firstBackups = backupFiles(configPath);

    const result = installClaudeDesktop(options);

    expect(result.status).toBe("already-configured");
    expect(result.backupCreated).toBe(false);
    expect(fs.readFileSync(configPath)).toEqual(afterFirstInstall);
    expect(backupFiles(configPath)).toEqual(firstBackups);
  });

  it("refuses to overwrite a conflicting noob server", () => {
    const root = makeTemporaryRoot();
    const configPath = makeOwnerConfig(root, {
      mcpServers: { noob: { command: "/different/node", args: ["other.js"] } },
    });
    const before = fs.readFileSync(configPath);

    expectInstallError(
      () => installClaudeDesktop(installOptions(root, configPath)),
      "noob_conflict",
    );

    expect(fs.readFileSync(configPath)).toEqual(before);
    expect(backupFiles(configPath)).toHaveLength(0);
  });

  it("rejects symlink, non-regular, and permissive config targets", () => {
    const root = makeTemporaryRoot();
    const pluginRoot = makePreparedPlugin(root);
    const realConfig = makeOwnerConfig(root, {});
    const symlinkPath = path.join(root, "symlink-config.json");
    fs.symlinkSync(realConfig, symlinkPath);
    expectInstallError(
      () =>
        installClaudeDesktop({
          configPath: symlinkPath,
          pluginRoot,
          nodeExecutable: process.execPath,
        }),
      "config_symlink",
    );

    const directoryPath = path.join(root, "config-directory");
    fs.mkdirSync(directoryPath);
    expectInstallError(
      () =>
        installClaudeDesktop({
          configPath: directoryPath,
          pluginRoot,
          nodeExecutable: process.execPath,
        }),
      "config_not_regular",
    );

    const permissivePath = makeOwnerConfig(path.join(root, "permissive"), {}, 0o644);
    expectInstallError(
      () =>
        installClaudeDesktop({
          configPath: permissivePath,
          pluginRoot,
          nodeExecutable: process.execPath,
        }),
      "config_permissions",
    );
  });

  it("rejects wrong-owner metadata independently of filesystem privileges", () => {
    const uid = typeof process.getuid === "function" ? process.getuid() : 501;
    expectInstallError(
      () =>
        validateConfigMetadata(
          {
            isSymbolicLink: () => false,
            isFile: () => true,
            uid: uid + 1,
            mode: 0o100600,
            size: 2,
          },
          uid,
        ),
      "config_wrong_owner",
    );
  });

  it("rejects invalid JSON roots and invalid mcpServers without mutation", () => {
    const root = makeTemporaryRoot();
    const pluginRoot = makePreparedPlugin(root);
    const configPath = makeOwnerConfig(root, {});
    fs.writeFileSync(configPath, "{invalid\n", { mode: 0o600 });
    fs.chmodSync(configPath, 0o600);
    expectInstallError(
      () => installClaudeDesktop({ configPath, pluginRoot, nodeExecutable: process.execPath }),
      "config_invalid_json",
    );

    writeJson(configPath, { mcpServers: [] });
    expectInstallError(
      () => installClaudeDesktop({ configPath, pluginRoot, nodeExecutable: process.execPath }),
      "config_invalid_mcp_servers",
    );
    expect(backupFiles(configPath)).toHaveLength(0);
  });

  it("requires aligned package and Claude manifest versions", () => {
    const root = makeTemporaryRoot();
    const configPath = makeOwnerConfig(root, {});
    const pluginRoot = makePreparedPlugin(root);
    writeJson(path.join(pluginRoot, ".claude-plugin", "plugin.json"), {
      name: "noob-plugin",
      version: "9.9.9",
    });

    expectInstallError(
      () => installClaudeDesktop({ configPath, pluginRoot, nodeExecutable: process.execPath }),
      "package_version_mismatch",
    );
    expect(backupFiles(configPath)).toHaveLength(0);
  });

  it("rejects a symlinked dist/main.js", () => {
    const root = makeTemporaryRoot();
    const configPath = makeOwnerConfig(root, {});
    const pluginRoot = makePreparedPlugin(root);
    const entrypoint = path.join(pluginRoot, "dist", "main.js");
    fs.unlinkSync(entrypoint);
    fs.symlinkSync(process.execPath, entrypoint);

    expectInstallError(
      () => installClaudeDesktop({ configPath, pluginRoot, nodeExecutable: process.execPath }),
      "entrypoint_invalid",
    );
    expect(backupFiles(configPath)).toHaveLength(0);
  });

  it("detects a target replacement before rename and leaves the replacement untouched", () => {
    const root = makeTemporaryRoot();
    const configPath = makeOwnerConfig(root, { preserved: "original" });
    const options = installOptions(root, configPath, {
      hooks: {
        beforeFinalValidation: () => {
          fs.renameSync(configPath, `${configPath}.moved-by-test`);
          writeJson(configPath, { preserved: "concurrent-writer" });
        },
      },
    });

    expectInstallError(() => installClaudeDesktop(options), "config_changed");

    expect(JSON.parse(fs.readFileSync(configPath, "utf8"))).toEqual({
      preserved: "concurrent-writer",
    });
    expect(backupFiles(configPath)).toHaveLength(1);
    expect(
      fs
        .readdirSync(path.dirname(configPath))
        .filter((entry) => entry.includes(".noob-install-") && entry.endsWith(".tmp")),
    ).toHaveLength(0);
  });

  it("returns fixed nonsecret status text", () => {
    expect(
      formatInstallStatus({ status: "installed", version: "0.2.0", backupCreated: true }),
    ).toBe("OK [installed]: N.O.O.B. 0.2.0 installed; protected backup created.");
    expect(
      formatInstallStatus({
        status: "already-configured",
        version: "0.2.0",
        backupCreated: false,
      }),
    ).not.toMatch(/[\\/]|mcpServers|command|args|cwd|token/i);
  });

  it("runs the CLI dry-run with fixed output and no filesystem mutation", () => {
    const root = makeTemporaryRoot();
    const pluginRoot = makePreparedPlugin(root);
    const scriptsDirectory = path.join(pluginRoot, "scripts");
    fs.mkdirSync(scriptsDirectory);
    const installerPath = path.join(scriptsDirectory, "install-claude-desktop.mjs");
    fs.copyFileSync(
      path.join(path.dirname(fileURLToPath(import.meta.url)), "install-claude-desktop.mjs"),
      installerPath,
    );
    const sentinel = "UNRELATED_PRIVATE_SENTINEL";
    const configPath = makeOwnerConfig(root, {
      privateSetting: sentinel,
      mcpServers: { other: { command: "/private/executable" } },
    });
    const before = fs.readFileSync(configPath);
    const beforeEntries = fs.readdirSync(path.dirname(configPath)).sort();

    const result = spawnSync(
      process.execPath,
      [installerPath, "--config", configPath, "--dry-run"],
      { encoding: "utf8", env: { PATH: process.env.PATH ?? "" } },
    );

    expect(result.status).toBe(0);
    expect(result.stdout).toBe("OK [dry-run]: N.O.O.B. 0.2.0 is ready; no files changed.\n");
    expect(result.stdout).not.toContain(sentinel);
    expect(result.stderr).toBe("");
    expect(fs.readFileSync(configPath)).toEqual(before);
    expect(fs.readdirSync(path.dirname(configPath)).sort()).toEqual(beforeEntries);
  });
});
