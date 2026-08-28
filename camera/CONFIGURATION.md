# Per-device configuration and provisioning

No working credential belongs in Git. This directory ignores `sdkconfig`,
`sdkconfig.local`, generated binaries, and a local `secrets/` directory.

For a one-off development build, an activated ESP-IDF 6.0.2 environment can
write the values through `idf.py menuconfig`:

```sh
cd /absolute/path/to/N.O.O.B/camera
idf.py set-target esp32
idf.py menuconfig
```

Under **N.O.O.B. environmental camera**, configure unique values for:

- `NOOB_CAMERA_PROVISIONING_POP`: the provisioning app's unique proof of
  possession, 8-96 characters;
- `NOOB_CAMERA_PROVISIONING_AP_KEY`: the temporary SoftAP's unique WPA2 key,
  8-63 characters;
- `NOOB_CAMERA_API_TOKEN`: the gateway-only bearer token, 32-96 high-entropy
  characters.

For a governed release, use the repository's bounded configurator instead of
placing values on a shell command line:

```sh
python3 camera/scripts/configure_release.py \
  --material-file "/absolute/owner-only/path/devices/noob-cam.json" \
  --device-label noob-cam \
  --generate-if-missing \
  --gateway-pairing-file "/absolute/owner-only/path/pairing/noob-cam.key"
```

The protected paths must be absolute, owned by the current user, outside the
Git repository, and inaccessible to group/other users. Generation is
create-only: an existing material or pairing file is never overwritten. The
command atomically renders the ignored `camera/sdkconfig` as mode `0600` and
prints only a pass/fail message, never the generated values. The separate
pairing file contains only the camera API value needed by the gateway; the
provisioning values remain in the per-device recovery record.

`camera/scripts/build_source.sh` forces ESP-IDF's supported
`KCONFIG_REPORT_VERBOSITY=quiet` mode and runs each fixed build/size action
through `run_redacted_idf.py`. The extra filter is required because ESP-IDF
6.0.2 can emit configured string mismatches before its formal report, even in
quiet mode. The runner loads the three local values only to replace any exact
occurrence before a line reaches stdout/stderr. Do not invoke `idf.py` directly
for a configured release build.

Do not paste those values into a ticket, README, shell argument, query string,
QR screenshot, or source file. The generated `sdkconfig` contains them and must
remain local. A production manufacturing flow should move them to a dedicated
provisioning partition and make flash encryption/secure boot a separately
reviewed eFuse ceremony.

The provisioning client uses Espressif Network Provisioning Security 1 over a
WPA2-protected local SoftAP. The firmware never prints the Wi-Fi payload. After
successful provisioning, the manager stores the network configuration in NVS,
stops the SoftAP, joins as a station, and starts the authenticated API only after
receiving a station IP.

## One-shot provisioning from the uConsole

[`scripts/provision_from_uconsole.py`](scripts/provision_from_uconsole.py)
provides a bounded uConsole path that does not require switching a Mac away from
its current network. It must run as root because it temporarily changes the
uConsole's NetworkManager connection:

1. It validates an owner-only per-device material JSON and an existing
   NetworkManager Wi-Fi profile.
2. It reads that profile's SSID and WPA-PSK in-process with `nmcli
   --show-secrets`; neither value is printed or persisted by the script.
3. It creates one mode-`0600` keyfile under
   `/run/NetworkManager/system-connections`, joins the camera's protected
   `NOOB-CAM-XXXXXX` setup AP, and imports the official Espressif `esp_prov`
   and protocomm Python runtime from explicit absolute paths.
4. It establishes Security 1, submits the target Wi-Fi configuration, removes
   the ephemeral profile and keyfile, and restores the original profile by
   UUID in an unconditional cleanup path.
5. It writes only a bounded, nonsecret mode-`0600` result under `/run`.

The reviewed launch path is
[`scripts/launch_uconsole_provision.sh`](scripts/launch_uconsole_provision.sh).
Before launching, place the already-created per-device JSON at the one fixed
root-only intake path. Do not print or reconstruct it in the shell:

```sh
sudo install -o root -g root -m 0600 \
  /absolute/root-only/path/noob-cam.json \
  /run/noob-camera-device-material.json
```

Then launch with the pinned, root-controlled runtime paths actually staged on
the uConsole:

```sh
sudo /bin/bash /opt/noob-cam/camera/scripts/launch_uconsole_provision.sh \
  --python-runtime /absolute/pinned/python-runtime \
  --provisioner /opt/noob-cam/camera/scripts/provision_from_uconsole.py \
  --target-profile Trusted-LAN \
  --camera-service-name NOOB-CAM-XXXXXX \
  --esp-prov-tool-dir /absolute/espressif/network_provisioning/tool/esp_prov \
  --protocomm-python-dir /absolute/esp-idf/components/protocomm/python \
  --interface wlan0
```

The helper refuses non-root execution, an unsafe or non-canonical path, a
group/other-writable runtime, a material file not owned by root with mode
`0400`/`0600`, an invalid service/profile/interface value, or any existing
`noob-camera-provision.service`. It creates a transient unit with:

- `LoadCredential=material:/run/noob-camera-device-material.json`;
- `StandardOutput=null` and `StandardError=null`;
- `--collect`, without `--wait`.

The internal service entry resolves
`$CREDENTIALS_DIRECTORY/material` and validates that private copy before
removing the fixed `/run/noob-camera-device-material.json` intake source. It
verifies the source is absent, revalidates that the credential copy remains
usable, validates all runtime values, and only then directly `exec`s the Python
provisioner. Source removal is intentionally inside the main service entry:
systemd evaluates `LoadCredential` separately for each unit command, so an
`ExecStartPre` removal would make credential setup for `ExecStart` fail. No
protected value is a unit argument, environment variable, console message, or
journal record. If credential loading or copy validation fails, the source is
not removed. If source cleanup, runtime validation, or unit creation fails, the
helper fails closed rather than replacing an existing unit or continuing with
an ambiguous credential state.

The launch returns before `wlan0` changes networks, allowing the transient
service to survive the expected SSH drop. After the uConsole rejoins its
original network, inspect only the nonsecret result:

```sh
sudo cat /run/noob-camera-provision-result.json
```

`status=credentials_applied` proves that the protected session accepted the
set/apply sequence and that local cleanup/restoration succeeded. It does **not**
prove the camera obtained a station address, advertised mDNS, served an
authenticated frame, or passed storage acceptance. Those remain separate live
checks. On interruption, a subsequent run reclaims only the script's fixed,
reserved ephemeral profile identity while an advisory lock prevents concurrent
runs.

The API token is intentionally distinct from the Wi-Fi credentials. The
gateway stores it in a root-readable appliance credential file and forwards it
only in the `Authorization` header. Operator renderers and agent tool results
must never receive it.

## Direct post-provision acceptance from the uConsole

[`scripts/accept_from_uconsole.py`](scripts/accept_from_uconsole.py) is the
bounded acceptance lane for a camera that has joined the private network. It is
not a discovery tool and it does not change NetworkManager state. Supply one
private literal address, the explicit API port, the full expected
`cam_<16 lowercase hex>` identity from the protected device record, and an
absolute canonical root-owned token file with mode `0400` or `0600`:

```sh
sudo /opt/noob-cam/tools/venv/bin/python \
  /opt/noob-cam/camera/scripts/accept_from_uconsole.py \
  --host 192.168.50.94 \
  --port 80 \
  --expected-device-id cam_0123456789abcdef \
  --token-file /etc/noob/environment-camera.key
```

The helper reads the bearer internally and places it only in the
`Authorization` header. It never accepts the bearer in an argument,
environment variable, or URL, and its sole output is one compact nonsecret JSON
pass/fail record. It validates:

- the unauthenticated well-known identity and exact capability set;
- a missing-credential challenge, a deliberately invalid bearer rejection,
  and query-credential rejection without exposing the real bearer;
- the authenticated full device identity and allowlisted status shape;
- the AI-Thinker pin map, verified OV2640 PID, initialized PSRAM, and a fresh
  exact 640x480 JPEG baseline;
- bounded frame-sequence and frame-hash progress;
- generation-checked disable and re-enable, including disabled capture
  rejection and a newly decoded frame after re-enable.

The camera is briefly disabled during this check. Run it only in a controlled
acceptance window. If an error occurs after disable is attempted, the helper
makes a bounded best-effort re-enable request before returning failure.

Storage mutation is skipped by default. `--storage-test` explicitly creates and
retains one snapshot plus one 1-second, 1-fps clip, then verifies only their
metadata. The separate `--delete-created-media` flag is valid only with
`--storage-test` and deletes only those two newly created IDs. This helper has
no format operation and never deletes pre-existing media.
