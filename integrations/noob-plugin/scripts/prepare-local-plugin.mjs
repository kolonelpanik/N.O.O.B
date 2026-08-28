#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const pluginRoot = path.resolve(scriptDirectory, "..");
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const minimumNodeMajor = 22;
const nodeMajor = Number.parseInt(process.versions.node.split(".")[0] ?? "", 10);

if (!Number.isInteger(nodeMajor) || nodeMajor < minimumNodeMajor) {
  throw new Error(
    `N.O.O.B. requires Node.js ${minimumNodeMajor} or newer; found ${process.versions.node}.`,
  );
}

function runNpm(args, label) {
  console.log(`\n=== ${label} ===`);
  const result = spawnSync(npmCommand, args, {
    cwd: pluginRoot,
    env: process.env,
    stdio: "inherit",
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${npmCommand} ${args.join(" ")} failed with status ${result.status}.`);
  }
}

function readJson(relativePath) {
  const absolutePath = path.join(pluginRoot, relativePath);
  return JSON.parse(fs.readFileSync(absolutePath, "utf8"));
}

function resolveManifestConfig(manifestPath, expectedReference) {
  const manifest = readJson(manifestPath);
  if (manifest.mcpServers !== expectedReference) {
    throw new Error(
      `${manifestPath} must reference ${expectedReference}; found ${String(manifest.mcpServers)}.`,
    );
  }
  return readJson(expectedReference.replace(/^\.\//, ""));
}

function requireServer(config, source) {
  const server = config?.mcpServers?.noob;
  if (!server || typeof server !== "object") {
    throw new Error(`${source} does not define mcpServers.noob.`);
  }
  if (server.command !== "node" || !Array.isArray(server.args)) {
    throw new Error(`${source} must launch the N.O.O.B. server through Node.js.`);
  }
  return server;
}

function safeEnvironment() {
  if (!process.env.PATH) {
    throw new Error("PATH is required to launch Node.js for the local MCP smoke test.");
  }
  return {
    PATH: process.env.PATH,
    LANG: "C",
    NO_COLOR: "1",
  };
}

function withTimeout(promise, milliseconds, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${label} timed out after ${milliseconds} ms.`)),
      milliseconds,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function smokeConfig({ label, server, expand }) {
  const [{ Client }, { StdioClientTransport }] = await Promise.all([
    import(
      pathToFileURL(
        path.join(pluginRoot, "node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js"),
      ).href
    ),
    import(
      pathToFileURL(
        path.join(pluginRoot, "node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js"),
      ).href
    ),
  ]);

  const transport = new StdioClientTransport({
    command: expand(server.command),
    args: server.args.map((argument) => expand(argument)),
    cwd: expand(server.cwd ?? "."),
    env: safeEnvironment(),
    stderr: "pipe",
  });
  let stderrBytes = 0;
  transport.stderr?.on("data", (chunk) => {
    stderrBytes += Buffer.byteLength(chunk);
  });

  const client = new Client(
    { name: `noob-${label}-preparation-smoke`, version: "1.0.0" },
    { capabilities: {} },
  );

  try {
    await withTimeout(client.connect(transport), 8_000, `${label} initialization`);
    const listed = await withTimeout(client.listTools(), 8_000, `${label} tools/list`);
    const names = listed.tools.map((tool) => tool.name);
    const uniqueNames = new Set(names);
    if (names.length === 0 || uniqueNames.size !== names.length) {
      throw new Error(`${label} returned an empty or duplicate MCP tool list.`);
    }
    for (const requiredName of [
      "noob_get_status",
      "noob_open_console",
      "noob_emergency_release_all",
    ]) {
      if (!uniqueNames.has(requiredName)) {
        throw new Error(`${label} did not expose required tool ${requiredName}.`);
      }
    }
    return { toolCount: names.length, stderrBytes };
  } finally {
    await client.close();
  }
}

runNpm(["ci", "--ignore-scripts", "--no-audit", "--no-fund"], "locked dependency install");
runNpm(["run", "check"], "TypeScript check");
runNpm(["test"], "test suite");
runNpm(["run", "build"], "production build");

const entrypoint = path.join(pluginRoot, "dist/main.js");
if (!fs.existsSync(entrypoint) || !fs.statSync(entrypoint).isFile()) {
  throw new Error("Production build did not create dist/main.js.");
}

const codexServer = requireServer(
  resolveManifestConfig(".codex-plugin/plugin.json", "./.mcp.json"),
  ".mcp.json",
);
const claudeServer = requireServer(
  resolveManifestConfig(".claude-plugin/plugin.json", "./claude/.mcp.json"),
  "claude/.mcp.json",
);

const codexResult = await smokeConfig({
  label: "codex",
  server: codexServer,
  expand: (value) => (value === "." ? pluginRoot : value),
});
const claudeResult = await smokeConfig({
  label: "claude",
  server: claudeServer,
  expand: (value) => value.replaceAll("${CLAUDE_PLUGIN_ROOT}", pluginRoot),
});

console.log("\nN.O.O.B. local plugin preparation passed.");
console.log(`- Node.js: ${process.versions.node}`);
console.log(`- Codex stdio tools: ${codexResult.toolCount}`);
console.log(`- Claude stdio tools: ${claudeResult.toolCount}`);
console.log(
  "- N.O.O.B. authentication and Codex/Claude client-global configuration were not requested or modified.",
);
