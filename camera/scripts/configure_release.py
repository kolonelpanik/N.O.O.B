#!/usr/bin/env python3
"""Create and apply a protected per-device camera release configuration.

The command deliberately never prints the generated values.  Per-device
material is kept outside the repository in an owner-only JSON file, while the
rendered ESP-IDF ``sdkconfig`` remains ignored and owner-readable only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final


CAMERA_ROOT: Final = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT: Final = CAMERA_ROOT.parent
SCHEMA: Final = "noob.camera-device-material.v1"
FIRMWARE_VERSION: Final = "0.2.0"
MAX_MATERIAL_BYTES: Final = 8 * 1024
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_-]+$")
SDKCONFIG_KEYS: Final = {
    "provisioning_pop": "CONFIG_NOOB_CAMERA_PROVISIONING_POP",
    "provisioning_ap_key": "CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY",
    "api_token": "CONFIG_NOOB_CAMERA_API_TOKEN",
}
REQUIRED_RELEASE_SETTINGS: Final = (
    'CONFIG_IDF_TARGET="esp32"',
    "CONFIG_ESPTOOLPY_FLASHMODE_DIO=y",
    'CONFIG_ESPTOOLPY_FLASHMODE="dio"',
    "CONFIG_ESPTOOLPY_FLASHFREQ_40M=y",
    'CONFIG_ESPTOOLPY_FLASHFREQ="40m"',
    "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y",
    'CONFIG_ESPTOOLPY_FLASHSIZE="4MB"',
    "CONFIG_PARTITION_TABLE_CUSTOM=y",
    'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"',
    'CONFIG_PARTITION_TABLE_FILENAME="partitions.csv"',
    "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
    "CONFIG_SPIRAM=y",
    "CONFIG_SPIRAM_IGNORE_NOTFOUND=y",
    "CONFIG_SPIRAM_USE_MALLOC=y",
    "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384",
    "CONFIG_ESP_INT_WDT=y",
    "CONFIG_ESP_TASK_WDT_EN=y",
    "CONFIG_ESP_TASK_WDT_INIT=y",
    "CONFIG_ESP_TASK_WDT_TIMEOUT_S=10",
)


class ConfigurationError(RuntimeError):
    """Raised when protected release configuration is absent or unsafe."""


@dataclass(frozen=True, slots=True)
class DeviceMaterial:
    device_label: str
    provisioning_pop: str
    provisioning_ap_key: str
    api_token: str


def _is_inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return False
    return True


def _require_external_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ConfigurationError(f"{label} must be an absolute path")
    resolved = path.resolve(strict=False)
    if _is_inside_repository(resolved):
        raise ConfigurationError(f"{label} must remain outside the Git repository")
    return resolved


def _secure_directory(path: Path) -> None:
    if path.exists():
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ConfigurationError("protected material parent must be a real directory")
        if metadata.st_uid != os.getuid():
            raise ConfigurationError("protected material parent must be owned by this user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ConfigurationError("protected material parent must not grant group/other access")
        return
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def _open_protected_read(path: Path):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ConfigurationError("protected material must be a regular file")
    if metadata.st_uid != os.getuid():
        os.close(descriptor)
        raise ConfigurationError("protected material must be owned by this user")
    if stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        os.close(descriptor)
        raise ConfigurationError("protected material must use mode 0400 or 0600")
    if metadata.st_size > MAX_MATERIAL_BYTES:
        os.close(descriptor)
        raise ConfigurationError("protected material is unexpectedly large")
    return os.fdopen(descriptor, "r", encoding="utf-8")


def _validate_value(value: object, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ConfigurationError(f"{label} length is outside the release contract")
    if not SAFE_VALUE.fullmatch(value):
        raise ConfigurationError(f"{label} contains unsupported characters")
    return value


def generate_material(path: Path, device_label: str) -> None:
    path = _require_external_absolute(path, "material file")
    device_label = _validate_value(device_label, "device label", 3, 64)
    _secure_directory(path.parent)
    payload = {
        "schema": SCHEMA,
        "device_label": device_label,
        "created_at": datetime.now(UTC).isoformat(),
        "provisioning_pop": secrets.token_urlsafe(18),
        "provisioning_ap_key": secrets.token_urlsafe(18),
        "api_token": secrets.token_urlsafe(48),
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def load_material(path: Path) -> DeviceMaterial:
    path = _require_external_absolute(path, "material file")
    try:
        with _open_protected_read(path) as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError("protected material could not be read") from error
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ConfigurationError("protected material schema is not supported")
    allowed = {
        "schema",
        "device_label",
        "created_at",
        "provisioning_pop",
        "provisioning_ap_key",
        "api_token",
    }
    if set(payload) != allowed:
        raise ConfigurationError("protected material fields do not match the release contract")
    return DeviceMaterial(
        device_label=_validate_value(payload["device_label"], "device label", 3, 64),
        provisioning_pop=_validate_value(
            payload["provisioning_pop"], "provisioning value", 8, 96
        ),
        provisioning_ap_key=_validate_value(
            payload["provisioning_ap_key"], "provisioning network key", 8, 63
        ),
        api_token=_validate_value(payload["api_token"], "camera API value", 32, 96),
    )


def _load_release_defaults() -> str:
    path = CAMERA_ROOT / "sdkconfig.defaults"
    if not path.is_file() or path.is_symlink():
        raise ConfigurationError("tracked sdkconfig.defaults is absent or unsafe")
    source = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines()]
    for setting in REQUIRED_RELEASE_SETTINGS:
        key = setting.split("=", 1)[0]
        if [line for line in lines if line.startswith(f"{key}=")] != [setting]:
            raise ConfigurationError(
                "sdkconfig.defaults does not contain the complete release contract"
            )
    protected_keys = {*SDKCONFIG_KEYS.values()}
    for line in lines:
        if any(line.startswith(f"{key}=") for key in protected_keys):
            raise ConfigurationError(
                "sdkconfig.defaults must not contain per-device release values"
            )
    return source.rstrip("\n") + "\n"


def validate_release_sdkconfig(path: Path) -> None:
    path = path.resolve(strict=True)
    if path.parent != CAMERA_ROOT.resolve() or path.name != "sdkconfig":
        raise ConfigurationError("sdkconfig must be the camera directory's ignored local file")
    if not path.is_file() or path.is_symlink():
        raise ConfigurationError("sdkconfig must be a regular non-symlink file")
    source = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines()]
    for setting in REQUIRED_RELEASE_SETTINGS:
        key = setting.split("=", 1)[0]
        if [line for line in lines if line.startswith(f"{key}=")] != [setting]:
            raise ConfigurationError(
                "sdkconfig does not match the deterministic release contract"
            )
    for key in (
        "CONFIG_NOOB_CAMERA_FIRMWARE_VERSION",
        *SDKCONFIG_KEYS.values(),
    ):
        if sum(line.startswith(f"{key}=") for line in lines) != 1:
            raise ConfigurationError("sdkconfig release fields are missing or duplicated")


def render_sdkconfig(path: Path, material: DeviceMaterial) -> None:
    path = path.resolve(strict=False)
    if path.parent != CAMERA_ROOT.resolve():
        raise ConfigurationError("sdkconfig must be the camera directory's ignored local file")
    if path.name != "sdkconfig":
        raise ConfigurationError("release configuration may update only camera/sdkconfig")
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise ConfigurationError("sdkconfig must be a regular non-symlink file")
    values = {
        "CONFIG_NOOB_CAMERA_FIRMWARE_VERSION": FIRMWARE_VERSION,
        SDKCONFIG_KEYS["provisioning_pop"]: material.provisioning_pop,
        SDKCONFIG_KEYS["provisioning_ap_key"]: material.provisioning_ap_key,
        SDKCONFIG_KEYS["api_token"]: material.api_token,
    }
    source = _load_release_defaults()
    rendered = source + "".join(
        f'{key}="{value}"\n' for key, value in values.items()
    )

    descriptor, temporary_name = tempfile.mkstemp(prefix=".sdkconfig.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    validate_release_sdkconfig(path)


def write_gateway_value(path: Path, material: DeviceMaterial) -> None:
    path = _require_external_absolute(path, "gateway pairing file")
    _secure_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, f"{material.api_token}\n".encode())
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material-file", type=Path)
    parser.add_argument("--device-label", default="noob-cam")
    parser.add_argument("--generate-if-missing", action="store_true")
    parser.add_argument("--sdkconfig", type=Path, default=CAMERA_ROOT / "sdkconfig")
    parser.add_argument("--gateway-pairing-file", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.validate_only:
        validate_release_sdkconfig(args.sdkconfig)
        print("PASS: sdkconfig matches the deterministic release contract")
        return 0
    if args.material_file is None:
        raise ConfigurationError("--material-file is required unless --validate-only is used")
    material_path = _require_external_absolute(args.material_file, "material file")
    if not material_path.exists():
        if not args.generate_if_missing:
            raise ConfigurationError("protected material is absent")
        generate_material(material_path, args.device_label)
    material = load_material(material_path)
    render_sdkconfig(args.sdkconfig, material)
    if args.gateway_pairing_file is not None:
        write_gateway_value(args.gateway_pairing_file, material)
    print(
        "PASS: protected per-device release configuration applied; "
        "no protected values were printed"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        raise SystemExit(f"ERROR: {error}") from error
