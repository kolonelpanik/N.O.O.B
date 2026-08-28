#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OPERATOR_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_SVG=$(CDPATH= cd -- "$OPERATOR_DIR/.." && pwd)/design/noob-icon.svg
OUTPUT_DIR="$OPERATOR_DIR/build"
ICONSET="$OUTPUT_DIR/noob.iconset"

if [[ ! -f "$SOURCE_SVG" ]]; then
  echo "Missing source icon: $SOURCE_SVG" >&2
  exit 2
fi
if [[ $(uname -s) != "Darwin" ]]; then
  echo "macOS icon generation requires sips and iconutil." >&2
  exit 3
fi

mkdir -p "$OUTPUT_DIR"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

TASK_TMP=$(mktemp -d "${TMPDIR:-/tmp}/noob-macos-icon.XXXXXX")
trap 'rm -rf "$TASK_TMP"' EXIT
/usr/bin/sips -s format png -z 1024 1024 "$SOURCE_SVG" --out "$TASK_TMP/source.png" >/dev/null

for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$TASK_TMP/source.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  doubled=$((size * 2))
  /usr/bin/sips -z "$doubled" "$doubled" "$TASK_TMP/source.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done

/usr/bin/iconutil -c icns "$ICONSET" -o "$OUTPUT_DIR/noob.icns"
echo "$OUTPUT_DIR/noob.icns"
