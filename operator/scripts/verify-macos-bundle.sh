#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OPERATOR_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
APP_PATH=${1:-}

if [[ -z "$APP_PATH" ]]; then
  APP_PATH=$(find "$OPERATOR_DIR/release" -maxdepth 3 -type d -name 'N.O.O.B.app' -print -quit 2>/dev/null || true)
fi
if [[ -z "$APP_PATH" || ! -d "$APP_PATH/Contents" ]]; then
  echo "Usage: npm run verify:mac -- /absolute/path/to/N.O.O.B.app" >&2
  exit 2
fi

PLIST="$APP_PATH/Contents/Info.plist"
ASAR="$APP_PATH/Contents/Resources/app.asar"
[[ -f "$PLIST" ]] || { echo "Missing Info.plist" >&2; exit 3; }
[[ -f "$ASAR" ]] || { echo "Missing app.asar" >&2; exit 4; }

bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST")
[[ "$bundle_id" == "com.neveroutofbounds.noob-operator" ]] || {
  echo "Unexpected bundle identifier: $bundle_id" >&2
  exit 5
}
/usr/libexec/PlistBuddy -c 'Print :NSLocalNetworkUsageDescription' "$PLIST" >/dev/null
/usr/libexec/PlistBuddy -c 'Print :NSBonjourServices:0' "$PLIST" | grep -Fx '_noob-kvm._tcp' >/dev/null

ASAR_BIN="$OPERATOR_DIR/node_modules/.bin/asar"
[[ -x "$ASAR_BIN" ]] || { echo "Missing local asar inspector" >&2; exit 6; }
archive_list=$("$ASAR_BIN" list "$ASAR")
if grep -E '/(\.env($|\.)|[^/]*(auth|token|secret)[^/]*\.(key|txt|json)|[^/]*\.(pem|p12|pfx)|known_hosts|devices\.json)$' <<<"$archive_list"; then
  echo "Packaged archive contains a forbidden credential or runtime-state filename" >&2
  exit 7
fi

if /usr/bin/codesign --verify --deep --strict "$APP_PATH" >/dev/null 2>&1; then
  signing="verified"
else
  signing="unsigned-local-build"
fi

printf 'BUNDLE_ID=%s\n' "$bundle_id"
printf 'BONJOUR_SERVICE=_noob-kvm._tcp\n'
printf 'APP_ASAR=present\n'
printf 'EMBEDDED_AUTH_FILENAMES=none\n'
printf 'SIGNING=%s\n' "$signing"
