# N.O.O.B. Operator Plugin

The N.O.O.B. plugin gives Codex/ChatGPT desktop, Claude Code, Claude Desktop, and other MCP clients one local-first control plane for a paired **NEVER OUT OF BOUNDS** appliance.

It is an `interactive-decoupled` MCP App: the agent-facing tools and the mounted operator widget share one server, but observation, device pairing, camera state, stored media, and target input remain separate proof and permission boundaries.

## Safety model

- The gateway remains bound to loopback on the uConsole. The plugin reaches it only through a pinned SSH tunnel.
- mDNS discovery is a bounded `_noob-kvm._tcp.local` lookup. A discovery record is never treated as identity or authorization.
- Manual probing accepts only private/link-local IP addresses or local hostnames by default.
- Pairing requires an explicit comparison with the trusted appliance. The
  returned eight-digit pairing code is the normal human display; the complete
  observed SSH SHA-256 fingerprint remains available for advanced verification
  and is the identity actually pinned.
- The SSH subprocess uses `BatchMode=yes`, `IdentitiesOnly=yes`, `IdentityAgent=none`, `StrictHostKeyChecking=yes`, and `ExitOnForwardFailure=yes` with argument-vector spawning.
- The gateway bearer is read from an owner-only local file. It is never returned to a model, widget, log, command argument, or environment inherited by SSH.
- Opening the widget is observe-only. It never silently acquires HID control or starts recording.
- Every HID action requires an active bounded control session and an exact-generation target-frame proof whose gateway request began no more than five seconds earlier. The gateway proves that the frame met its configured freshness bound when served; because it does not expose a hardware capture timestamp, the plugin reports observation time and never invents capture time.
- A control session is released after at most five seconds without a validated HID action. An in-flight bounded action is not treated as idle, but it never extends the session's absolute deadline.
- A UART acknowledgement proves transport only. A newer frame is required before claiming target-visible acceptance.
- Media is addressed only by opaque IDs. There is no arbitrary path, shell, raw UART, delete-all, SD-format, or unbounded recording tool.
- “Camera off” means sensor/stream disabled while the controller remains powered and reachable. Electrical 5V removal requires separate switchable hardware.

## Build and validate

```bash
cd integrations/noob-plugin
npm ci
npm test
npm run check
npm run build
npm audit --audit-level=low
```

The build emits:

- `dist/main.js` — local stdio and authenticated loopback Streamable HTTP entry point;
- `dist/widget/widget/operator-console.html` — one bundled MCP Apps resource;
- `.codex-plugin/plugin.json` — Codex plugin manifest;
- `.claude-plugin/plugin.json` and `.mcp.json` — Claude plugin configuration;
- `mcpb/manifest.json` — Claude Desktop bundle source manifest.

## Protected local configuration

Defaults on macOS:

```text
~/Library/Application Support/N.O.O.B/devices.json
~/Library/Application Support/N.O.O.B/known_hosts
~/Library/Application Support/N.O.O.B/gateway.token
~/.ssh/id_ed25519_noob_uconsole
```

Defaults on Linux:

```text
~/.config/noob/devices.json
~/.config/noob/known_hosts
~/.config/noob/gateway.token
~/.ssh/id_ed25519_noob_uconsole
```

The support directory must be mode `0700`; the identity, token, known-hosts, and device files must be owner-only. `devices.json` uses the canonical [version-2 schema](../../docs/device-store-v2.schema.json) shared with Electron. Both clients migrate their legacy version-1 layouts under a cross-process lock and append to `known_hosts` without deleting unrelated entries. Identity, endpoint, unsupported-version, or same-host key conflicts fail closed. Override locations only with absolute paths:

```text
NOOB_PLUGIN_SUPPORT_DIR
NOOB_PLUGIN_CONFIG
NOOB_KNOWN_HOSTS_FILE
NOOB_SSH_IDENTITY_FILE
NOOB_GATEWAY_TOKEN_FILE
NOOB_SSH_USER
NOOB_REMOTE_GATEWAY_PORT
```

The token file contains only the gateway bearer and a trailing newline. Never commit it.

## Local clients

For a clean-clone, secret-free installation walkthrough for Codex, Claude Code,
and Claude Desktop, see [Local installation](LOCAL_INSTALL.md).

For Codex, install the plugin source through a personal marketplace. The validated plugin manifest points at `dist/main.js --stdio`.

For Claude Code, load the plugin directory during development:

```bash
claude --plugin-dir /absolute/path/to/integrations/noob-plugin
```

For a direct MCP configuration, use the appropriate wrapper from `openai.mcp.json` or `.mcp.json`. Replace plugin-root variables only through the host’s supported plugin loader—do not invoke a shell wrapper.

For Claude Desktop distribution, validate, pack, sign, and verify the MCPB after the final production build. The checked-in `mcpb/manifest.json` is source metadata, not a signed release artifact.

## ChatGPT Developer Mode

The same server has a loopback-only Streamable HTTP mode:

```bash
NOOB_MCP_HTTP_TOKEN='<owner-generated-value>' \
NOOB_MCP_ALLOWED_ORIGINS='https://chatgpt.com' \
node dist/main.js --http
```

HTTP mode refuses to start without its own bearer, binds only `127.0.0.1`, and checks `Origin` when present. A public ChatGPT connection needs a reviewed HTTPS/OAuth deployment or an authenticated outbound companion/relay; do not expose the uConsole gateway or this unaudited development endpoint directly to the internet.

## Normal workflow

1. `noob_discover_devices` or `noob_probe_device` returns an untrusted candidate.
2. The operator independently compares the short pairing code shown on the
   trusted appliance; the complete SSH fingerprint remains available for an
   advanced audit.
3. `noob_register_device` pins that identity.
4. `noob_connect_device` opens the loopback SSH tunnel.
5. `noob_get_status` and `noob_get_frame` establish current visual proof.
6. Observation can continue indefinitely without a control lease.
7. `noob_list_media` and `noob_get_media` inspect camera-owned items;
   `noob_get_clip_frame` retrieves exactly one completed clip frame at a bounded
   `0..149` index through the fixed gateway route.
8. Target input requires `noob_acquire_control`, a fresh target frame token, one explicit HID action, a newer verification frame, and `noob_release_control`. The plugin also releases abandoned control automatically at the advertised idle or absolute deadline.
9. `noob_emergency_release_all` remains available as a safety-restoring action.

No automatic recording occurs. Screenshots and bounded camera clips are always explicit operator or agent actions.
Stopping a clip cancels it and removes the unpublished partial; it does not
finalize or publish a shortened recording.
