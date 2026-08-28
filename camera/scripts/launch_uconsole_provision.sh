#!/usr/bin/env bash
# Launch the N.O.O.B. camera provisioner with systemd credential isolation.

set -euo pipefail

readonly UNIT_NAME="noob-camera-provision.service"
readonly MATERIAL_SOURCE="/run/noob-camera-device-material.json"
readonly MAX_MATERIAL_BYTES=8192

fail() {
    printf 'ERROR [%s]: %s\n' "$1" "$2" >&2
    exit 1
}

validate_service_name() {
    [[ "$1" =~ ^NOOB-CAM-[0-9A-Fa-f]{6}$ ]] ||
        fail invalid_service_name "camera service name must match NOOB-CAM-XXXXXX"
}

validate_profile_name() {
    local value=$1
    local pattern='^[A-Za-z0-9_. -]{1,128}$'
    [[ "$value" =~ $pattern && "$value" != -* ]] ||
        fail invalid_profile "target profile name contains unsupported characters"
}

validate_interface_name() {
    [[ "$1" =~ ^[A-Za-z0-9_.:-]{1,32}$ ]] ||
        fail invalid_interface "Wi-Fi interface name is invalid"
}

canonical_existing_path() {
    local value=$1
    local label=$2
    [[ "$value" == /* ]] || fail invalid_path "$label must be absolute"
    local safe_path_pattern='^[A-Za-z0-9_./:+@ -]+$'
    [[ "$value" =~ $safe_path_pattern ]] ||
        fail invalid_path "$label contains characters unsafe for a transient unit"
    local canonical
    canonical=$(/usr/bin/realpath -e -- "$value" 2>/dev/null) ||
        fail invalid_path "$label does not exist"
    [[ "$canonical" == "$value" ]] ||
        fail invalid_path "$label must be canonical and contain no symlink"
    printf '%s\n' "$canonical"
}

validate_root_path_chain() {
    local value=$1
    local label=$2
    local current=$value
    while [[ "$current" != "/" ]]; do
        [[ ! -L "$current" ]] || fail unsafe_path "$label contains a symlink"
        local owner mode mode_value
        owner=$(/usr/bin/stat -c '%u' -- "$current" 2>/dev/null) ||
            fail unsafe_path "$label could not be inspected"
        mode=$(/usr/bin/stat -c '%a' -- "$current" 2>/dev/null) ||
            fail unsafe_path "$label could not be inspected"
        [[ "$owner" == "0" ]] || fail unsafe_path "$label must be root-owned"
        mode_value=$((8#$mode))
        (( (mode_value & 8#22) == 0 )) ||
            fail unsafe_path "$label must not be group/other writable"
        current=${current%/*}
        [[ -n "$current" ]] || current="/"
    done
}

validate_root_regular_file() {
    local value label
    value=$(canonical_existing_path "$1" "$2") || return
    label=$2
    [[ -f "$value" && ! -L "$value" ]] ||
        fail unsafe_file "$label must be a regular non-symlink file"
    validate_root_path_chain "$value" "$label"
}

validate_root_python_runtime() {
    local value=$1
    local label="Python runtime"
    [[ "$value" == /* ]] || fail invalid_path "$label must be absolute"
    local safe_path_pattern='^[A-Za-z0-9_./:+@ -]+$'
    [[ "$value" =~ $safe_path_pattern ]] ||
        fail invalid_path "$label contains characters unsafe for a transient unit"

    local lexical
    lexical=$(/usr/bin/realpath -m -s -- "$value" 2>/dev/null) ||
        fail invalid_path "$label could not be normalized"
    [[ "$lexical" == "$value" ]] ||
        fail invalid_path "$label path must be absolute and canonical"

    local current=$value target candidate owner depth=0
    while [[ -L "$current" ]]; do
        (( depth += 1 ))
        (( depth <= 16 )) || fail unsafe_runtime "$label symlink chain is too deep"

        owner=$(/usr/bin/stat -c '%u' -- "$current" 2>/dev/null) ||
            fail unsafe_runtime "$label symlink could not be inspected"
        [[ "$owner" == "0" ]] || fail unsafe_runtime "$label symlink must be root-owned"
        # Linux symlink mode bits are always 0777 and are not used for access
        # control. Protect the link through root ownership and its checked,
        # non-writable parent path; protect every resolved node separately.
        validate_root_path_chain "$(/usr/bin/dirname -- "$current")" "$label parent path"

        target=$(/usr/bin/readlink -- "$current" 2>/dev/null) ||
            fail unsafe_runtime "$label symlink target could not be read"
        if [[ "$target" == /* ]]; then
            candidate=$target
        else
            candidate="$(/usr/bin/dirname -- "$current")/${target}"
        fi
        lexical=$(/usr/bin/realpath -m -s -- "$candidate" 2>/dev/null) ||
            fail unsafe_runtime "$label symlink target could not be normalized"
        [[ "$lexical" == /* ]] ||
            fail unsafe_runtime "$label symlink target did not resolve absolutely"
        current=$lexical
    done

    [[ -e "$current" ]] || fail unsafe_runtime "$label symlink chain is broken"
    [[ -f "$current" && ! -L "$current" ]] ||
        fail unsafe_runtime "$label must resolve to a regular file"
    validate_root_path_chain "$current" "$label resolved path"
    [[ -x "$current" ]] || fail unsafe_runtime "$label resolved target is not executable"
}

validate_root_directory() {
    local value label
    value=$(canonical_existing_path "$1" "$2") || return
    label=$2
    [[ -d "$value" && ! -L "$value" ]] ||
        fail unsafe_directory "$label must be a real directory"
    validate_root_path_chain "$value" "$label"
}

validate_material_file() {
    local value=$1
    validate_root_regular_file "$value" "protected camera material"
    local mode size
    mode=$(/usr/bin/stat -c '%a' -- "$value")
    [[ "$mode" == "400" || "$mode" == "600" ]] ||
        fail unsafe_material "protected camera material must use mode 0400 or 0600"
    size=$(/usr/bin/stat -c '%s' -- "$value")
    [[ "$size" =~ ^[0-9]+$ ]] || fail unsafe_material "protected material size is invalid"
    (( size > 0 && size <= MAX_MATERIAL_BYTES )) ||
        fail unsafe_material "protected camera material is empty or unexpectedly large"
}

remove_material_source_after_credential_copy() {
    local source=$1
    # The systemd credential copy has already been validated by the caller.
    # Revalidate the fixed intake source immediately before removing it so a
    # missing or replaced source fails closed rather than being ignored.
    validate_material_file "$source"
    /usr/bin/rm -f -- "$source" ||
        fail source_cleanup_failed "protected camera material source could not be removed"
    [[ ! -e "$source" && ! -L "$source" ]] ||
        fail source_cleanup_failed "protected camera material source still exists"
}

validate_runtime_values() {
    validate_service_name "$CAMERA_SERVICE_NAME"
    validate_profile_name "$TARGET_PROFILE"
    validate_interface_name "$INTERFACE_NAME"
    validate_root_python_runtime "$PYTHON_RUNTIME"
    validate_root_regular_file "$PROVISIONER" "camera provisioner"
    validate_root_directory "$ESP_PROV_TOOL_DIR" "Espressif esp_prov tool directory"
    validate_root_directory "$PROTOCOMM_PYTHON_DIR" "Espressif protocomm Python directory"
}

usage() {
    printf '%s\n' \
        "Usage: $0 --python-runtime PATH --provisioner PATH" \
        "          --esp-prov-tool-dir PATH --protocomm-python-dir PATH" \
        "          --target-profile NAME --camera-service-name NOOB-CAM-XXXXXX" \
        "          [--interface wlan0]" >&2
}

parse_runtime_args() {
    PYTHON_RUNTIME=""
    PROVISIONER=""
    ESP_PROV_TOOL_DIR=""
    PROTOCOMM_PYTHON_DIR=""
    TARGET_PROFILE=""
    CAMERA_SERVICE_NAME=""
    INTERFACE_NAME="wlan0"
    local interface_seen=0
    while (( $# )); do
        case "$1" in
            --python-runtime|--provisioner|--esp-prov-tool-dir|--protocomm-python-dir|--target-profile|--camera-service-name|--interface)
                (( $# >= 2 )) || fail invalid_arguments "$1 requires one value"
                local option=$1 value=$2
                shift 2
                case "$option" in
                    --python-runtime) [[ -z "$PYTHON_RUNTIME" ]] || fail invalid_arguments "duplicate $option"; PYTHON_RUNTIME=$value ;;
                    --provisioner) [[ -z "$PROVISIONER" ]] || fail invalid_arguments "duplicate $option"; PROVISIONER=$value ;;
                    --esp-prov-tool-dir) [[ -z "$ESP_PROV_TOOL_DIR" ]] || fail invalid_arguments "duplicate $option"; ESP_PROV_TOOL_DIR=$value ;;
                    --protocomm-python-dir) [[ -z "$PROTOCOMM_PYTHON_DIR" ]] || fail invalid_arguments "duplicate $option"; PROTOCOMM_PYTHON_DIR=$value ;;
                    --target-profile) [[ -z "$TARGET_PROFILE" ]] || fail invalid_arguments "duplicate $option"; TARGET_PROFILE=$value ;;
                    --camera-service-name) [[ -z "$CAMERA_SERVICE_NAME" ]] || fail invalid_arguments "duplicate $option"; CAMERA_SERVICE_NAME=$value ;;
                    --interface) (( interface_seen == 0 )) || fail invalid_arguments "duplicate $option"; INTERFACE_NAME=$value; interface_seen=1 ;;
                esac
                ;;
            *) fail invalid_arguments "unsupported argument" ;;
        esac
    done
    [[ -n "$PYTHON_RUNTIME" && -n "$PROVISIONER" && -n "$ESP_PROV_TOOL_DIR" &&
       -n "$PROTOCOMM_PYTHON_DIR" && -n "$TARGET_PROFILE" &&
       -n "$CAMERA_SERVICE_NAME" ]] || {
        usage
        fail invalid_arguments "all required runtime values must be supplied"
    }
}

build_systemd_run_command() {
    local launcher_path=$1
    SYSTEMD_RUN_COMMAND=(
        /usr/bin/systemd-run
        "--unit=${UNIT_NAME}"
        --collect
        --property=Type=exec
        --property=User=root
        --property=Group=root
        --property=UMask=0077
        --property=NoNewPrivileges=yes
        --property=PrivateTmp=yes
        --property=RestrictSUIDSGID=yes
        --property=StandardOutput=null
        --property=StandardError=null
        "--property=LoadCredential=material:${MATERIAL_SOURCE}"
        /bin/bash
        "$launcher_path"
        --service-entry
        --python-runtime "$PYTHON_RUNTIME"
        --provisioner "$PROVISIONER"
        --esp-prov-tool-dir "$ESP_PROV_TOOL_DIR"
        --protocomm-python-dir "$PROTOCOMM_PYTHON_DIR"
        --target-profile "$TARGET_PROFILE"
        --camera-service-name "$CAMERA_SERVICE_NAME"
        --interface "$INTERFACE_NAME"
    )
}

service_entry() {
    parse_runtime_args "$@"
    [[ ${EUID} -eq 0 ]] || fail root_required "service entry must run as root"
    [[ -n ${CREDENTIALS_DIRECTORY:-} ]] ||
        fail missing_credential "systemd did not supply a credential directory"
    local credentials_directory material
    credentials_directory=$(canonical_existing_path "$CREDENTIALS_DIRECTORY" "credential directory")
    [[ "$credentials_directory" == /run/credentials/* ]] ||
        fail unsafe_credential "credential directory is outside systemd runtime credentials"
    validate_root_directory "$credentials_directory" "credential directory"
    material="${credentials_directory}/material"
    validate_material_file "$material"
    [[ "$material" != "$MATERIAL_SOURCE" ]] ||
        fail unsafe_credential "systemd credential copy aliases the intake source"

    # LoadCredential is evaluated for each command in a unit. The source must
    # remain present until this main service process starts. Remove it only
    # after the private credential copy is established and validated.
    remove_material_source_after_credential_copy "$MATERIAL_SOURCE"
    validate_material_file "$material"

    # A bad runtime still fails closed without leaving the protected intake.
    validate_runtime_values
    exec "$PYTHON_RUNTIME" "$PROVISIONER" \
        --material-file "$material" \
        --target-profile "$TARGET_PROFILE" \
        --camera-service-name "$CAMERA_SERVICE_NAME" \
        --esp-prov-tool-dir "$ESP_PROV_TOOL_DIR" \
        --protocomm-python-dir "$PROTOCOMM_PYTHON_DIR" \
        --interface "$INTERFACE_NAME"
}

launch() {
    parse_runtime_args "$@"
    [[ ${EUID} -eq 0 ]] || fail root_required "launcher must run as root"
    [[ -x /usr/bin/systemd-run && -x /usr/bin/systemctl && -x /bin/bash ]] ||
        fail systemd_unavailable "required systemd commands are unavailable"
    validate_material_file "$MATERIAL_SOURCE"
    validate_runtime_values
    local launcher_path load_state
    launcher_path=$(canonical_existing_path "${BASH_SOURCE[0]}" "launch helper")
    validate_root_regular_file "$launcher_path" "launch helper"
    load_state=$(/usr/bin/systemctl show --property=LoadState --value "$UNIT_NAME" 2>/dev/null) ||
        fail unit_check_failed "existing transient unit state could not be checked"
    [[ "$load_state" == "not-found" ]] ||
        fail unit_exists "refusing to replace an existing camera provisioning unit"
    build_systemd_run_command "$launcher_path"
    "${SYSTEMD_RUN_COMMAND[@]}" >/dev/null 2>&1 ||
        fail launch_failed "systemd rejected the transient provisioning unit"
    printf '%s\n' \
        "PASS: isolated camera provisioning unit launched; reconnect before reading the nonsecret result"
}

main() {
    if [[ ${1:-} == "--service-entry" ]]; then
        shift
        service_entry "$@"
    else
        launch "$@"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
