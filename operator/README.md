# N.O.O.B Operator

Electron control surface for the N.O.O.B gateway. The renderer is sandboxed and
has no Node.js access. The gateway bearer token and active control lease live
only in Electron main-process memory.

## Local tunnel

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

## Install and run

```sh
npm ci
npm run build
npm start
```

Paste the authorized bearer token into the app's authentication dialog. It is
sent directly to Electron main through the narrow preload bridge, cleared from
the field immediately, and never stored in renderer storage.

The control rail exposes the uConsole's built-in keyboard and trackball as a
separate, explicit input mode. It can be armed only while Electron does not own
the remote-control lease. Disarming remains available as the safe action, and
the gateway bearer token stays confined to Electron main-process memory.

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
