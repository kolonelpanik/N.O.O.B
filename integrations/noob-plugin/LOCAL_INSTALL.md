# Prepare a fresh local plugin checkout

The repository intentionally does not commit `node_modules/` or generated
`dist/` output. Prepare a fresh public Git clone before registering the plugin
with Codex or loading it in Claude Code.

Requirements:

- Node.js 22 or newer
- npm
- a network connection for the locked dependency download

From this directory, run one command:

```sh
npm run prepare:local
```

The command performs a bounded, reproducible preparation:

1. installs the exact `package-lock.json` dependency graph with
   `npm ci --ignore-scripts`;
2. runs the TypeScript check and test suite;
3. builds the widget and stdio MCP server into `dist/`;
4. validates both client manifests; and
5. starts the built server with clean Codex-style and Claude-style environments,
   performs MCP initialization, and lists tools without calling any tool.

It creates only the ignored local `node_modules/` and `dist/` directories. It
does not add a marketplace, register a plugin, edit Codex or Claude settings,
read a N.O.O.B. bearer token, or contact an appliance.

When the command prints `N.O.O.B. local plugin preparation passed`, register or
load this same prepared directory using the client-specific workflow.

For an already configured Codex personal marketplace whose `noob-plugin` entry
points at this directory:

```sh
codex plugin add noob-plugin@personal
```

For Claude Code development loading:

```sh
claude plugin validate "$PWD" --strict
claude --plugin-dir "$PWD"
```

Use `/mcp` in Claude Code to confirm the plugin-provided `noob` server. Plugin
registration is deliberately separate from preparation so the script is safe
to run in CI and in a disposable checkout.

## Install into Claude Desktop on macOS

Claude Desktop uses its owner configuration at:

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

Prepare this plugin first with `npm run prepare:local`, then quit Claude Desktop
so it cannot concurrently rewrite its configuration. The installer requires the
configuration to already exist as a regular, non-symlink file owned by the
current user with exact mode `0600`. Inspect it before changing permissions:

```sh
CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
test -f "$CLAUDE_CONFIG" && test ! -L "$CLAUDE_CONFIG"
stat -f 'owner=%Su mode=%Sp' "$CLAUDE_CONFIG"
chmod 600 "$CLAUDE_CONFIG"
```

Run the non-mutating preflight first, then perform the bounded merge:

```sh
node scripts/install-claude-desktop.mjs --dry-run
node scripts/install-claude-desktop.mjs
```

The installer:

- resolves this prepared plugin root and the currently running Node executable
  to canonical absolute paths;
- validates a nonempty `dist/main.js`, the package name, and matching package,
  Claude-plugin, and MCPB versions;
- adds only `mcpServers.noob`, preserving every unrelated JSON value;
- refuses to replace any different pre-existing `mcpServers.noob` entry;
- treats an exact existing N.O.O.B. entry as an idempotent success;
- writes an exact-byte, uniquely named, timestamped mode-`0600` backup beside
  the configuration before mutation; and
- writes a mode-`0600` replacement in the same directory and atomically renames
  it over the validated original after a final change check.

Dry-run creates no files. Both success and error output use fixed status text;
the installer never prints the existing configuration, unrelated server
definitions, credentials, or a backup/config path. A nonstandard configuration
can be tested explicitly only with an absolute path:

```sh
node scripts/install-claude-desktop.mjs \
  --config /absolute/path/to/claude_desktop_config.json \
  --dry-run
```

After a successful install, launch Claude Desktop yourself and verify that the
`noob` MCP server is present before attempting device pairing. This installer
does not launch the app, read N.O.O.B. credentials, connect to an appliance, or
modify Codex/Claude Code configuration.
