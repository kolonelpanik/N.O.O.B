# N.O.O.B Operator

Electron control surface for the N.O.O.B gateway. The renderer is sandboxed and
has no Node.js access. The gateway bearer token and active control lease live
only in Electron main-process memory.

## Choose and connect to an appliance

The **Devices** dialog provides two bounded connection paths:

- **Scan network** listens for `_noob-kvm._tcp` advertisements for two seconds.
  Discovery is only a hint; it never establishes trust by itself.
- **Manual address** accepts a private IPv4 address, IPv4 link-local address,
  local/ULA IPv6 address or `.local` name. Public addresses, URLs, paths,
  single-label names, and `user@host` strings are rejected.

In both paths the operator inspects the appliance's SSH host key and must enter
the eight-digit code displayed on the trusted uConsole before the device can be
pinned. The complete `SHA256:` fingerprint remains available under **Advanced
SSH fingerprint** for audits and independent records. The app then creates a
loopback-only SSH tunnel using strict host-key checking.
Pinned profiles contain only the device address, public host key, fingerprint,
and non-secret connection metadata. They do not contain a password, bearer
token, or SSH private key.

The default SSH identity is:

```text
~/.ssh/id_ed25519_noob_uconsole
```

It must be a regular owner-private file. A custom absolute key path can be
provided with `NOOB_SSH_IDENTITY_FILE`; this setting identifies an external key
and does not copy it into the application bundle or operator data directory.
The uConsole must advertise `_noob-kvm._tcp` for automatic discovery. If it
does not, use its private IP address in the manual field.

Pairing is a one-time trust step. The selected device becomes the default; a
normal later app launch reconnects it automatically. If its private address
changes, bounded discovery may refresh the address only when the observed full
SSH key still matches the stored pin. Unknown or changed keys remain blocked.
Electron and the MCP plugin share canonical `devices.json` version 2 and merge
`known_hosts` append-only, including safe in-place migration of both legacy
version-1 layouts. On macOS both use
`~/Library/Application Support/N.O.O.B`; packaged Electron never substitutes
its package-name-derived `noob-operator` directory. The
`NOOB_OPERATOR_SUPPORT_DIR` override is retained for an explicit absolute test
or managed-deployment path.

See [Mac operator packaging and pairing](../docs/macos-operator.md) for the
complete build, installation, trust, and release-signing procedure.

## Existing local tunnel

SYNAPSE-S2 already owns local port `8765`, so the operator defaults to
`http://127.0.0.1:18765`. Establish the forward in a separate terminal after
verifying the uConsole SSH host key:

```sh
ssh -N -L 127.0.0.1:18765:127.0.0.1:8765 <user>@192.0.2.83
```

The mapping is local `127.0.0.1:18765` to uConsole `127.0.0.1:8765`. Confirm
the public process probe without exposing credentials:

```sh
curl --fail http://127.0.0.1:18765/healthz
```

For a different local forward, set `NOOB_GATEWAY_URL` when starting the app.
Do not put the bearer token in that URL, an environment variable, a shell
argument, or command history.

This development fallback remains useful when a tunnel is already managed
outside the app.

## Install and run from source

```sh
npm ci
npm run build
npm start
```

Paste the authorized bearer token into the app's authentication dialog. It is
sent directly to Electron main through the narrow preload bridge, cleared from
the field immediately, and never stored in renderer storage.
For managed one-click startup, the main process can instead read the plugin's
externally provisioned owner-only `gateway.token`; Electron never writes that
file or sends its contents to the renderer.

## Build a desktop-launchable Mac app

Create a local unsigned app bundle and inspect its security-sensitive metadata:

```sh
npm ci
npm run package:mac
npm run verify:mac -- "$PWD/release/mac-arm64/N.O.O.B.app"
```

Install it for the current user and add a desktop launcher without modifying
`/Applications`:

```sh
../packaging/install-macos-operator.sh "$PWD/release/mac-arm64/N.O.O.B.app"
```

Use `--replace` only when intentionally replacing a prior user-local install,
or `--no-desktop-link` to install only in `~/Applications`. The installer does
not launch the app and does not copy a token, password, SSH private key,
`known_hosts`, or saved-device store. `package:mac` is explicitly an unsigned
local engineering build. External distribution requires Developer ID signing
and Apple notarization; the presence of a `.app` alone is not release proof.

The control rail exposes the uConsole's built-in keyboard and trackball as a
separate, explicit input mode. It can be armed only while Electron does not own
the remote-control lease. Disarming remains available as the safe action, and
the gateway bearer token stays confined to Electron main-process memory.

## Target and environmental views

The source tabs keep the two camera roles explicit:

- **Target** is the HDMI capture path and the only view that can accept HID
  control. Switching away from it first releases this Electron lease and
  disarms any exclusive uConsole input grab; the switch fails closed if either
  release is unconfirmed.
- **Environment** is a non-target observation camera. Keyboard, pointer, agent,
  and local-input controls are removed from this view. Its On/Off control
  enables or disables the ESP32 sensor and network stream only. It does not
  claim to switch USB power, which remains physically on.

Both sources have fit, 50–200% zoom, screenshot, and full-screen controls.
**Screenshot** downloads the current gateway JPEG to the operator workstation
for either source and does not require or mutate camera microSD storage.
**Store camera snapshot** is the separate, generation-checked environmental
camera write; completed items appear in Recent media.

Environmental recording is always explicit. The operator can choose a bounded
1–30 second clip at 1–5 fps, with no more than 150 frames, watch the job's
progress, and request a bounded stop. There is no automatic recording. Recent
microSD snapshots and first-frame clip previews are read through opaque media
IDs; the renderer has no delete, format, arbitrary URL, or filesystem-path
operation.

The desktop operator is single-instance. A second launch restores, shows, and
focuses the existing window instead of creating another gateway client,
renderer, or control lifecycle.

For a managed workstation, the safer first-run path is an inherited one-shot
pipe. The value is consumed by Electron main before the window opens and is
never placed in an argument, environment variable, renderer, clipboard, or
persistent local store:

```sh
ssh <user>@192.0.2.83 'sudo -u noob cat /etc/noob/auth.key' \
  | ./node_modules/.bin/electron . --auth-stdin
```

The pipe accepts exactly one 32–256 byte printable ASCII value plus an optional
line ending, is capped at 258 bytes, and is zeroed after parsing. If it is
missing or malformed, startup fails closed to the normal authentication dialog.

Development mode is available with `npm run dev`. Validation commands are:

```sh
npm test
npm run lint
npm audit --audit-level=high
```
