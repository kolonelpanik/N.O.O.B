# Mac operator packaging and appliance pairing

The N.O.O.B. Mac operator is a sandboxed Electron renderer backed by a narrow
main-process bridge. Its product path preserves the project's loopback-first
gateway boundary: finding an appliance does not turn its LAN address into a
direct HTTP control endpoint. The app identifies the appliance, pins its SSH
host key, opens a loopback-only SSH forward, and then authenticates to the
gateway through that tunnel.

## What gets stored

The application writes connection metadata beneath its macOS user-data
directory (normally `~/Library/Application Support/N.O.O.B`):

- `devices.json` uses the canonical, versioned
  [`device-store-v2`](device-store-v2.schema.json) shape shared by Electron and
  the MCP plugin. It contains a default profile, LAN address, SSH port, public
  SSH host key and fingerprint, gateway port, and advertised capability labels.
- `known_hosts` contains the public SSH host keys accepted through explicit
  identity confirmation. Both clients merge new pins into this file and never
  regenerate it from a partial device list, so comments, unrelated entries,
  and prior addresses survive.

The directory is forced to mode `0700`; both files are forced to mode `0600`.
Neither file is an authentication secret. The following material is
deliberately excluded:

- The gateway bearer token stays only in Electron main-process memory and is
  cleared on device change or explicit sign-out. For one-click managed
  startup, Electron may read an externally provisioned owner-only
  `gateway.token` from this support directory; it never creates, rewrites, or
  exposes that file to the renderer.
- The SSH private key remains at an external, owner-private absolute path. It
  is never copied into the app or its user-data directory.
- No password is accepted, persisted, or passed to SSH. SSH runs in batch mode
  with agent use disabled.
- Renderer storage cannot read the bearer token, active lease, SSH key, or raw
  `known_hosts` content.

Do not put the bearer token in a URL, environment variable, shell argument,
desktop file, application bundle, or screenshot. When no owner-only managed
token file exists, the packaged flow asks for it after the SSH tunnel is
proven. The existing `--auth-stdin` development bootstrap remains available
when a managed one-shot pipe is required.

## First connection

Before opening the operator, provision an SSH key dedicated to the authorized
uConsole account. The default path is:

```text
~/.ssh/id_ed25519_noob_uconsole
```

The app rejects a missing key, a non-regular path, and a key whose group or
other permission bits are set. A custom absolute path can be selected at app
startup with `NOOB_SSH_IDENTITY_FILE`. The operator also supports
`NOOB_SSH_USER` and `NOOB_REMOTE_GATEWAY_PORT`; none of these settings contains
authentication material.

In the operator:

1. On the trusted uConsole, read the `PAIRING · 0000-0000` value shown directly
   in the N.O.O.B. Local Console header. The installed `noob-pairing-code`
   terminal command displays the same code plus the full advanced fingerprint.
2. Open **Devices** once. Use **Scan network** or enter the uConsole's
   private/local address and SSH port manually.
3. Select **Inspect**, compare the `0000-0000` code on both displays, type that
   code, and choose **Pair and connect**. Discovery output alone is not identity
   proof; the explicit comparison is the trust step.
4. **Advanced SSH fingerprint** retains the complete `SHA256:` identity for
   audits and independent records. Use it whenever a short-code comparison is
   not made directly against the trusted appliance display.
5. After the pinned SSH tunnel reaches the loopback gateway health endpoint,
   enter the gateway bearer token if no managed owner-only token file exists.
   When that managed file is present, the newly paired gateway client consumes
   it immediately; a second application launch is not required.

After that first explicit pairing, opening N.O.O.B. is the normal quick-start
path. The app reconnects the default pin before showing the operator. If its
saved private address no longer answers, one bounded Bonjour lookup may update
the route only when the newly observed full SSH key matches the existing pin.
Unknown or changed keys are never accepted automatically; the app remains
disconnected and requires a new explicit trust decision.

## One-click direct control

With the target feed live, choose **Take control** once. That trusted click
requests relative pointer lock, disarms and verifies any active uConsole-local
keyboard/trackball grab, claims the Mac operator lease, selects Human mode, and
enables keyboard and pointer capture together. The separate capture toggles
remain available for advanced opt-out and recovery, but they are not part of
the normal startup path.

Direct control remains active only while the N.O.O.B. window stays focused.
Press **Escape** or **RELEASE ALL INPUT** to release immediately. Focus loss,
pointer-lock loss during acquisition, an unconfirmed uConsole handoff, a stale
claim, and lease or serial failure all fail closed and clear captured input.

Version-1 Electron camelCase and plugin snake_case stores are migrated in
place under a cross-process lock. Migration preserves valid devices and an
existing default; a sole legacy Electron pin becomes the default. Duplicate
identities, one endpoint mapped to different keys, unsupported future
versions, and same-endpoint `known_hosts` key conflicts fail closed without
rewriting the conflicting source.

Automatic discovery is intentionally bounded to the `_noob-kvm._tcp` Bonjour
service. The app accepts only private, link-local, ULA, or `.local` addresses.
It does not scan arbitrary port ranges and it does
not accept a public URL. If mDNS is unavailable or the appliance is not yet
advertising the service, manual private-address entry is the supported
fallback. The reviewed uConsole publisher, fixed non-secret TXT surface,
installation steps, and rollback are documented in
[Appliance discovery](appliance-discovery.md). Discovery remains separate from
the environmental camera's `_noobcam._tcp` service.

Changing devices releases any Electron-owned input lease, clears the current
bearer token, closes the old tunnel, and requires authentication against the
new gateway. A tunnel is created with these protections:

- argument-vector execution of `/usr/bin/ssh`, never a shell;
- `BatchMode=yes`, `IdentitiesOnly=yes`, and `IdentityAgent=none`;
- `StrictHostKeyChecking=yes` against the app's owner-private pinned file;
- `ExitOnForwardFailure=yes` and bounded connection/keepalive settings;
- an ephemeral listener on `127.0.0.1` forwarding only to appliance
  `127.0.0.1:8765` by default.

## Build and inspect a local app

Requirements are macOS, Node.js/npm, the repository checkout, and Xcode command
line tools providing `sips`, `iconutil`, `codesign`, and `plutil`.

From `operator/`:

```sh
npm ci
npm test
npm run lint
npm run build
npm audit --audit-level=high
npm run package:mac
npm run verify:mac -- "$PWD/release/mac-arm64/N.O.O.B.app"
```

`package:mac` creates an unsigned local engineering bundle on Apple Silicon at
`operator/release/mac-arm64/N.O.O.B.app`. The verifier checks the fixed bundle
identifier, Local Network usage description, Bonjour service declaration,
ASAR presence, and absence of credential/runtime-state filenames. It reports
the signing state rather than treating an unsigned bundle as distributable.

From the repository root, install the inspected bundle for the current user:

```sh
./packaging/install-macos-operator.sh \
  "$PWD/operator/release/mac-arm64/N.O.O.B.app"
```

This stages the bundle into `~/Applications/N.O.O.B.app` and creates
`~/Desktop/N.O.O.B.app` as a symlink to it. It does not launch the program or
mutate any live connection state. Use `--no-desktop-link` to omit the desktop
link. Use `--replace` only after closing N.O.O.B. and intentionally replacing a
previous user-local install; the old app is preserved with a timestamped
backup name.

## Signed distribution boundary

An app produced by `package:mac` is useful for local engineering acceptance,
but it is not a signed or notarized release. External distribution requires:

1. a valid Apple Developer ID Application signing identity;
2. hardened-runtime signing with the repository entitlements;
3. Apple notarization credentials supplied by the release environment, never
   committed to this repository;
4. successful notarization submission, acceptance, stapling, Gatekeeper
   assessment, and a clean-machine launch test;
5. integrity evidence for the final DMG/ZIP artifact.

With release credentials available in a protected CI or release environment,
`npm run dist:mac` is the signed-distribution entry point. A successful local
build or deep `codesign` check is not notarization evidence. Keep local build,
signed artifact, notarized artifact, and clean-machine operator acceptance as
separate proof layers.

## Acceptance checklist

- [ ] Scan results are presented as untrusted hints and expire quickly.
- [ ] Manual entry rejects public IPs, URLs, paths, and `user@host` syntax.
- [ ] First pairing is blocked until the exact appliance-displayed short code
      is entered; the complete fingerprint remains available in Advanced.
- [ ] The pinned host key is re-used under strict host checking on reconnect.
- [ ] A normal subsequent app launch reconnects the default pin without a scan
      or new trust prompt; moved-address recovery succeeds only for the same
      full pinned key.
- [ ] Electron and the plugin both read canonical store v2 and preserve
      unrelated `known_hosts` content during migration and updates.
- [ ] The SSH identity is external and owner-private.
- [ ] The generated app archive contains no token, key, known-host, or saved
      device state file.
- [ ] The bearer token is cleared when switching appliances.
- [ ] The old input lease is released before a device switch.
- [ ] The desktop icon opens the user-local application bundle.
- [ ] Signed/notarized release claims are made only with matching Apple and
      clean-machine evidence.
