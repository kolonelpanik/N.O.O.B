#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CAMERA_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$CAMERA_ROOT"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "ERROR: idf.py is not available; install and activate ESP-IDF 6.0.2." >&2
    exit 2
fi

IDF_VERSION=$(idf.py --version 2>&1)
case "$IDF_VERSION" in
    *"v6.0.2"*) ;;
    *)
        echo "ERROR: expected ESP-IDF v6.0.2, observed: $IDF_VERSION" >&2
        exit 3
        ;;
esac

if git -C "$CAMERA_ROOT/.." ls-files --error-unmatch camera/sdkconfig >/dev/null 2>&1; then
    echo "ERROR: camera/sdkconfig is tracked and may contain credentials." >&2
    exit 4
fi

if [ ! -f sdkconfig ]; then
    echo "ERROR: sdkconfig is absent. Run scripts/configure_release.py with protected per-device material first." >&2
    exit 5
fi
if ! python3 "$SCRIPT_DIR/configure_release.py" \
    --sdkconfig sdkconfig --validate-only; then
    echo "ERROR: sdkconfig is outside the deterministic release contract." >&2
    exit 6
fi

# ESP-IDF emits configured string mismatches before its formal Kconfig report,
# including when its supported quiet mode is active.  Every IDF action therefore
# runs through a fixed-command filter which loads the three local release values
# only to replace any occurrence before the line reaches stdout/stderr.
export KCONFIG_REPORT_VERBOSITY=quiet
REDACTED_IDF="$SCRIPT_DIR/run_redacted_idf.py"
if [ ! -x "$REDACTED_IDF" ]; then
    echo "ERROR: protected ESP-IDF runner is absent or not executable." >&2
    exit 9
fi

python3 "$REDACTED_IDF" --sdkconfig sdkconfig build
if ! python3 "$SCRIPT_DIR/configure_release.py" \
    --sdkconfig sdkconfig --validate-only; then
    echo "ERROR: ESP-IDF resolved sdkconfig outside the deterministic release contract." >&2
    exit 10
fi
python3 "$REDACTED_IDF" --sdkconfig sdkconfig size
python3 "$REDACTED_IDF" --sdkconfig sdkconfig size-components

BIN=build/noob_camera.bin
if [ ! -f "$BIN" ]; then
    echo "ERROR: expected application binary $BIN was not produced." >&2
    exit 7
fi

SIZE=$(wc -c < "$BIN" | tr -d ' ')
MAX=$((1445 * 1024))
if [ "$SIZE" -gt "$MAX" ]; then
    echo "ERROR: application binary is $SIZE bytes; release-headroom ceiling is $MAX." >&2
    exit 8
fi

echo "PASS: source build completed without flashing; app bytes=$SIZE ceiling=$MAX"
