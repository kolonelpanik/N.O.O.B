#!/bin/sh

# Install the pinned CircuitPython build and N.O.O.B firmware onto a Pico WH.
# The script deliberately leaves CIRCUITPY storage and CDC enabled until the
# complete stuck-input/recovery acceptance suite has passed.

set -eu

usage() {
    printf '%s\n' "Usage: $0 --uf2 FILE --bundle FILE [--volume-root DIR]" >&2
    exit 2
}

uf2=''
bundle=''
volume_root='/Volumes'

while [ "$#" -gt 0 ]; do
    case "$1" in
        --uf2)
            [ "$#" -ge 2 ] || usage
            uf2=$2
            shift 2
            ;;
        --bundle)
            [ "$#" -ge 2 ] || usage
            bundle=$2
            shift 2
            ;;
        --volume-root)
            [ "$#" -ge 2 ] || usage
            volume_root=$2
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[ -f "$uf2" ] || usage
[ -f "$bundle" ] || usage
[ -d "$volume_root" ] || {
    printf 'Volume root does not exist: %s\n' "$volume_root" >&2
    exit 3
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
rp2_volume="$volume_root/RPI-RP2"
circuitpy_volume="$volume_root/CIRCUITPY"

if [ -d "$rp2_volume" ]; then
    info_file="$rp2_volume/INFO_UF2.TXT"
    if [ ! -f "$info_file" ] || ! grep -q 'Board-ID: RPI-RP2' "$info_file"; then
        printf '%s\n' 'RPI-RP2 is mounted but its UF2 identity is unexpected; refusing to flash.' >&2
        exit 4
    fi

    printf '%s\n' 'Installing CircuitPython UF2...'
    cp "$uf2" "$rp2_volume/"
    sync

    tries=0
    while [ "$tries" -lt 60 ] && [ ! -d "$circuitpy_volume" ]; do
        sleep 0.5
        tries=$((tries + 1))
    done
fi

if [ ! -d "$circuitpy_volume" ]; then
    printf '%s\n' 'CIRCUITPY did not enumerate. Re-enter BOOTSEL and rerun this installer.' >&2
    exit 5
fi

boot_out="$circuitpy_volume/boot_out.txt"
if [ -f "$boot_out" ]; then
    printf '%s\n' 'Detected CircuitPython runtime:'
    sed -n '1,4p' "$boot_out"
fi

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/noob-pico.XXXXXX")
trap 'rm -rf "$temporary_dir"' EXIT HUP INT TERM

bundle_prefix=$(
    unzip -Z1 "$bundle" |
        sed -n 's#\(.*\)lib/adafruit_hid/__init__\.mpy#\1#p' |
        head -1
)

if [ -z "$bundle_prefix" ]; then
    printf '%s\n' 'The bundle does not contain lib/adafruit_hid/__init__.mpy.' >&2
    exit 6
fi

unzip -q "$bundle" "${bundle_prefix}lib/adafruit_hid/*" -d "$temporary_dir"
hid_source="$temporary_dir/${bundle_prefix}lib/adafruit_hid"
[ -d "$hid_source" ] || {
    printf '%s\n' 'Could not extract adafruit_hid from the bundle.' >&2
    exit 7
}

mkdir -p "$circuitpy_volume/lib"
rm -rf "$circuitpy_volume/lib/adafruit_hid"
cp -R "$hid_source" "$circuitpy_volume/lib/adafruit_hid"
cp "$repo_dir/pico/boot.py" "$circuitpy_volume/boot.py"
cp "$repo_dir/pico/protocol.py" "$circuitpy_volume/protocol.py"
cp "$repo_dir/pico/code.py" "$circuitpy_volume/code.py"
sync

printf '%s\n' 'N.O.O.B Pico files installed:'
for installed in boot.py protocol.py code.py lib/adafruit_hid/__init__.mpy; do
    test -f "$circuitpy_volume/$installed"
    printf '  %s\n' "$installed"
done

printf '%s\n' 'Waiting briefly for CircuitPython to reload...'
sleep 3
if [ -f "$boot_out" ]; then
    sed -n '1,4p' "$boot_out"
fi
printf '%s\n' 'Pico install complete; validate the CDC console and UART ready event next.'
