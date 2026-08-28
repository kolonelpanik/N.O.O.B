#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=launch_uconsole_provision.sh
source "${SCRIPT_DIR}/launch_uconsole_provision.sh"

expect_failure() {
    if ( "$@" ) >/dev/null 2>&1; then
        printf 'expected failure: %q' "$1" >&2
        return 1
    fi
}

validate_service_name NOOB-CAM-89418C
expect_failure validate_service_name NOOB-CAM-anything
validate_profile_name Trusted-LAN
validate_profile_name "Lab Wi-Fi"
expect_failure validate_profile_name --unsafe-option
validate_interface_name wlan0
expect_failure validate_interface_name 'wlan0;touch'

PYTHON_RUNTIME=/opt/noob-runtime/bin/python
PROVISIONER=/opt/noob-cam/camera/scripts/provision_from_uconsole.py
ESP_PROV_TOOL_DIR=/opt/noob-cam/network_provisioning/tool/esp_prov
PROTOCOMM_PYTHON_DIR=/opt/esp/idf/components/protocomm/python
TARGET_PROFILE=Trusted-LAN
CAMERA_SERVICE_NAME=NOOB-CAM-89418C
INTERFACE_NAME=wlan0
build_systemd_run_command /opt/noob-cam/camera/scripts/launch_uconsole_provision.sh

rendered=$(printf '%s\n' "${SYSTEMD_RUN_COMMAND[@]}")
grep -Fx -- '--collect' <<<"$rendered" >/dev/null
if grep -Fx -- '--wait' <<<"$rendered" >/dev/null; then
    printf '%s\n' 'unexpected --wait in transient unit command' >&2
    exit 1
fi
grep -Fx -- '--property=StandardOutput=null' <<<"$rendered" >/dev/null
grep -Fx -- '--property=StandardError=null' <<<"$rendered" >/dev/null
grep -Fx -- '--property=LoadCredential=material:/run/noob-camera-device-material.json' <<<"$rendered" >/dev/null
if grep -F -- 'ExecStartPre=' <<<"$rendered" >/dev/null; then
    printf '%s\n' 'unexpected credential-source deletion before main service entry' >&2
    exit 1
fi
grep -Fx -- '--service-entry' <<<"$rendered" >/dev/null
if grep -F -- 'provisioning_pop' <<<"$rendered" >/dev/null ||
   grep -F -- 'provisioning_ap_key' <<<"$rendered" >/dev/null ||
   grep -F -- 'api_token' <<<"$rendered" >/dev/null; then
    printf '%s\n' 'protected field name appeared in unit command' >&2
    exit 1
fi

if (( EUID == 0 )); then
    root_test_dir=/run/noob-launch-helper-test.$$
    trap 'rm -rf -- "$root_test_dir"' EXIT
    install -d -o root -g root -m 0700 "$root_test_dir"
    printf '%s\n' '{"fixture":"nonsecret"}' >"$root_test_dir/material.json"
    chmod 0600 "$root_test_dir/material.json"
    validate_material_file "$root_test_dir/material.json"
    chmod 0644 "$root_test_dir/material.json"
    expect_failure validate_material_file "$root_test_dir/material.json"

    install -o root -g root -m 0755 /bin/true "$root_test_dir/python-real"
    # Match the live venv/system shape: the venv link is absolute, while the
    # root-controlled system link uses a relative filename in the same dir.
    ln -s -- python-real "$root_test_dir/python3"
    ln -s -- "$root_test_dir/python3" "$root_test_dir/python"
    validate_root_python_runtime "$root_test_dir/python"

    chmod 0775 "$root_test_dir/python-real"
    expect_failure validate_root_python_runtime "$root_test_dir/python"
    chmod 0755 "$root_test_dir/python-real"

    ln -s -- missing-python "$root_test_dir/python-broken"
    expect_failure validate_root_python_runtime "$root_test_dir/python-broken"

    credential_dir="$root_test_dir/credentials"
    install -d -o root -g root -m 0700 "$credential_dir"
    source_material="$root_test_dir/source-material.json"
    credential_material="$credential_dir/material"
    printf '%s\n' '{"fixture":"copy-remains-usable"}' >"$source_material"
    chmod 0600 "$source_material"
    cp -- "$source_material" "$credential_material"
    chmod 0400 "$credential_material"
    validate_material_file "$credential_material"
    [[ -f "$source_material" ]] || exit 1
    remove_material_source_after_credential_copy "$source_material"
    [[ ! -e "$source_material" && ! -L "$source_material" ]] || exit 1
    validate_material_file "$credential_material"
    grep -Fx -- '{"fixture":"copy-remains-usable"}' "$credential_material" >/dev/null
fi

printf '%s\n' 'PASS: uConsole provisioning launcher shell contract'
