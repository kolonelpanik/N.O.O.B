#!/usr/bin/env python3
"""Provision one N.O.O.B. camera from a NetworkManager-managed uConsole.

The command is deliberately one-shot.  It reads the existing target Wi-Fi
profile and the camera's protected release material into this process, joins
the camera setup AP through a root-only keyfile under ``/run``, and calls the
official Espressif Security 1 Python client as a library.  Protected values are
never placed in argv, the environment, stdout/stderr, or the result document.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Final, NoReturn, Protocol, Sequence


MATERIAL_SCHEMA: Final = "noob.camera-device-material.v1"
RESULT_SCHEMA: Final = "noob.camera-provision-result.v1"
SETUP_CONNECTION_ID: Final = "noob-camera-provision-ephemeral"
SETUP_CONNECTION_UUID: Final = "a9761a8c-f090-5b43-b415-a1be8a730307"
DEFAULT_ENDPOINT: Final = "192.168.4.1:80"
DEFAULT_RESULT_PATH: Final = Path("/run/noob-camera-provision-result.json")
DEFAULT_KEYFILE_PATH: Final = Path(
    "/run/NetworkManager/system-connections/noob-camera-provision.nmconnection"
)
DEFAULT_LOCK_PATH: Final = Path("/run/lock/noob-camera-provision.lock")
MAX_MATERIAL_BYTES: Final = 8 * 1024
MAX_COMMAND_BYTES: Final = 16 * 1024
SAFE_SECRET = re.compile(r"^[A-Za-z0-9_-]+$")
CAMERA_SERVICE = re.compile(r"^NOOB-CAM-[0-9A-Fa-f]{6}$")
PROFILE_SELECTOR = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")


class ProvisioningError(RuntimeError):
    """A failure safe to summarize without copying protected input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class _DiscardOutput:
    """A non-buffering text sink for noisy third-party client diagnostics."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None


DISCARD_OUTPUT: Final = _DiscardOutput()


@dataclass(frozen=True, slots=True)
class CameraMaterial:
    provisioning_pop: str
    provisioning_ap_key: str


@dataclass(frozen=True, slots=True)
class WifiProfile:
    connection_uuid: str
    ssid: str
    psk: str


@dataclass(slots=True)
class RunState:
    stage: str = "preflight"
    setup_removed: bool = False
    restore: str = "not_attempted"


class Runner(Protocol):
    def run(
        self, argv: Sequence[str], *, timeout: int = 30, allow_failure: bool = False
    ) -> bytes: ...


class SubprocessRunner:
    """Run a fixed command without inheriting output or accepting stdin."""

    def run(
        self, argv: Sequence[str], *, timeout: int = 30, allow_failure: bool = False
    ) -> bytes:
        try:
            completed = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProvisioningError(
                "command_unavailable", "a required local networking command failed"
            ) from error
        if len(completed.stdout) > MAX_COMMAND_BYTES or len(completed.stderr) > MAX_COMMAND_BYTES:
            raise ProvisioningError(
                "command_output_too_large", "a local networking response exceeded its bound"
            )
        if completed.returncode != 0 and not allow_failure:
            raise ProvisioningError(
                "networkmanager_failed", "NetworkManager rejected a bounded provisioning action"
            )
        return completed.stdout if completed.returncode == 0 else b""


def _fail(code: str, message: str) -> NoReturn:
    raise ProvisioningError(code, message)


def _validate_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute():
        _fail("invalid_path", f"{label} must be an absolute path")
    return path.resolve(strict=False)


def _secure_regular_file(path: Path, label: str, expected_uid: int) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProvisioningError("protected_file_unavailable", f"{label} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail("unsafe_protected_file", f"{label} must be a regular non-symlink file")
    if metadata.st_uid != expected_uid:
        _fail("unsafe_protected_file", f"{label} has an unexpected owner")
    if stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        _fail("unsafe_protected_file", f"{label} must use mode 0400 or 0600")
    return metadata


def _open_protected(path: Path, expected_uid: int):
    _secure_regular_file(path, "camera material", expected_uid)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
        or metadata.st_size > MAX_MATERIAL_BYTES
    ):
        os.close(descriptor)
        _fail("unsafe_protected_file", "camera material changed during validation")
    return os.fdopen(descriptor, "r", encoding="utf-8")


def _bounded_secret(value: object, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail("invalid_material", f"{label} is outside the release contract")
    if not SAFE_SECRET.fullmatch(value):
        _fail("invalid_material", f"{label} contains unsupported characters")
    return value


def load_camera_material(path: Path, *, expected_uid: int) -> CameraMaterial:
    path = _validate_absolute(path, "camera material")
    try:
        with _open_protected(path, expected_uid) as handle:
            payload = json.load(handle)
    except ProvisioningError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProvisioningError(
            "invalid_material", "camera material could not be decoded"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema") != MATERIAL_SCHEMA:
        _fail("invalid_material", "camera material schema is not supported")
    expected_fields = {
        "schema",
        "device_label",
        "created_at",
        "provisioning_pop",
        "provisioning_ap_key",
        "api_token",
    }
    if set(payload) != expected_fields:
        _fail("invalid_material", "camera material fields do not match the release contract")
    return CameraMaterial(
        provisioning_pop=_bounded_secret(
            payload.get("provisioning_pop"), "provisioning proof", 8, 96
        ),
        provisioning_ap_key=_bounded_secret(
            payload.get("provisioning_ap_key"), "setup network key", 8, 63
        ),
    )


def _decode_single_value(raw: bytes, label: str, *, maximum: int = 256) -> str:
    if not raw or len(raw) > maximum:
        _fail("invalid_profile", f"{label} is absent or outside its bound")
    try:
        value = raw.decode("utf-8").rstrip("\n")
    except UnicodeError as error:
        raise ProvisioningError("invalid_profile", f"{label} is not valid UTF-8") from error
    if "\n" in value or "\r" in value or "\x00" in value:
        _fail("invalid_profile", f"{label} contains unsupported control characters")
    return value


def validate_wifi_profile(connection_uuid: str, ssid: str, psk: str) -> WifiProfile:
    try:
        normalized_uuid = str(uuid.UUID(connection_uuid))
    except (ValueError, AttributeError) as error:
        raise ProvisioningError("invalid_profile", "target profile UUID is invalid") from error
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        _fail("invalid_profile", "target profile SSID is outside the Wi-Fi bound")
    if any(character in ssid for character in ("\x00", "\r", "\n")):
        _fail("invalid_profile", "target profile SSID contains unsupported characters")
    valid_psk = 8 <= len(psk) <= 63 or (
        len(psk) == 64 and all(character in "0123456789abcdefABCDEF" for character in psk)
    )
    if not valid_psk or any(character in psk for character in ("\x00", "\r", "\n")):
        _fail("invalid_profile", "target profile does not contain a bounded WPA-PSK")
    return WifiProfile(normalized_uuid, ssid, psk)


def read_wifi_profile(runner: Runner, profile_name: str, interface: str) -> WifiProfile:
    if not PROFILE_SELECTOR.fullmatch(profile_name):
        _fail("invalid_argument", "target NetworkManager profile name is invalid")
    prefix = ["/usr/bin/nmcli", "--terse", "--escape", "no"]
    connection_uuid = _decode_single_value(
        runner.run(prefix + ["--get-values", "connection.uuid", "connection", "show", "id", profile_name]),
        "target profile UUID",
    )
    connection_type = _decode_single_value(
        runner.run(prefix + ["--get-values", "connection.type", "connection", "show", "uuid", connection_uuid]),
        "target profile type",
    )
    if connection_type not in {"802-11-wireless", "wifi"}:
        _fail("invalid_profile", "target profile is not a Wi-Fi connection")
    ssid = _decode_single_value(
        runner.run(prefix + ["--get-values", "802-11-wireless.ssid", "connection", "show", "uuid", connection_uuid]),
        "target profile SSID",
    )
    psk = _decode_single_value(
        runner.run(
            prefix
            + [
                "--show-secrets",
                "--get-values",
                "802-11-wireless-security.psk",
                "connection",
                "show",
                "uuid",
                connection_uuid,
            ]
        ),
        "target profile WPA-PSK",
    )
    profile = validate_wifi_profile(connection_uuid, ssid, psk)
    active_uuid = _decode_single_value(
        runner.run(
            prefix
            + [
                "--get-values",
                "GENERAL.CON-UUID",
                "device",
                "show",
                interface,
            ]
        ),
        "active Wi-Fi profile UUID",
    )
    if active_uuid != profile.connection_uuid:
        _fail(
            "target_profile_not_active",
            "target Wi-Fi profile is not active on the selected interface",
        )
    return profile


def render_setup_keyfile(service_name: str, setup_key: str, interface: str) -> bytes:
    if not CAMERA_SERVICE.fullmatch(service_name):
        _fail("invalid_argument", "camera service name must match NOOB-CAM-XXXXXX")
    if not INTERFACE_NAME.fullmatch(interface):
        _fail("invalid_argument", "Wi-Fi interface name is invalid")
    setup_key = _bounded_secret(setup_key, "setup network key", 8, 63)
    text = (
        "[connection]\n"
        f"id={SETUP_CONNECTION_ID}\n"
        f"uuid={SETUP_CONNECTION_UUID}\n"
        "type=wifi\n"
        f"interface-name={interface}\n"
        "autoconnect=false\n\n"
        "[wifi]\n"
        "mode=infrastructure\n"
        f"ssid={service_name}\n\n"
        "[wifi-security]\n"
        "key-mgmt=wpa-psk\n"
        f"psk={setup_key}\n\n"
        "[ipv4]\nmethod=auto\n\n"
        "[ipv6]\nmethod=disabled\n\n"
        "[proxy]\n"
    )
    return text.encode("utf-8")


def _write_exclusive_root_file(path: Path, payload: bytes) -> None:
    path = _validate_absolute(path, "ephemeral keyfile")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise ProvisioningError(
            "ephemeral_keyfile_failed", "ephemeral NetworkManager keyfile could not be created"
        ) from error
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("ephemeral_keyfile_failed", "ephemeral keyfile write did not complete")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_unlink_keyfile(path: Path, expected_uid: int) -> None:
    if not path.exists() and not path.is_symlink():
        return
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        _fail("unsafe_ephemeral_file", "refusing to remove an unexpected keyfile object")
    path.unlink()


class NetworkManager:
    def __init__(
        self, runner: Runner, *, interface: str, keyfile_path: Path, expected_uid: int
    ) -> None:
        self.runner = runner
        self.interface = interface
        self.keyfile_path = keyfile_path
        self.expected_uid = expected_uid
        self.cleanup_authorized = False

    def _nmcli(self, *arguments: str, timeout: int = 30, allow_failure: bool = False) -> bytes:
        return self.runner.run(
            ["/usr/bin/nmcli", *arguments], timeout=timeout, allow_failure=allow_failure
        )

    def prepare(self, service_name: str, setup_key: str) -> None:
        existing = self._nmcli(
            "--terse",
            "--escape",
            "no",
            "--get-values",
            "connection.id",
            "connection",
            "show",
            "uuid",
            SETUP_CONNECTION_UUID,
            allow_failure=True,
        )
        if existing:
            existing_id = _decode_single_value(existing, "stale setup profile ID")
            if existing_id != SETUP_CONNECTION_ID:
                _fail("profile_collision", "reserved setup profile UUID is already in use")
        self.cleanup_authorized = True
        if existing:
            self._nmcli("connection", "delete", "uuid", SETUP_CONNECTION_UUID)
        _safe_unlink_keyfile(self.keyfile_path, self.expected_uid)
        payload = render_setup_keyfile(service_name, setup_key, self.interface)
        _write_exclusive_root_file(self.keyfile_path, payload)
        self._nmcli("connection", "reload")

    def activate_setup(self) -> None:
        self._nmcli(
            "--wait",
            "45",
            "connection",
            "up",
            "uuid",
            SETUP_CONNECTION_UUID,
            "ifname",
            self.interface,
            timeout=55,
        )

    def remove_setup(self) -> bool:
        if not self.cleanup_authorized:
            return True
        removed = True
        try:
            self._nmcli(
                "connection", "delete", "uuid", SETUP_CONNECTION_UUID, allow_failure=True
            )
            _safe_unlink_keyfile(self.keyfile_path, self.expected_uid)
            self._nmcli("connection", "reload", allow_failure=True)
        except ProvisioningError:
            removed = False
        return removed

    def restore(self, profile: WifiProfile) -> bool:
        try:
            self._nmcli(
                "--wait",
                "60",
                "connection",
                "up",
                "uuid",
                profile.connection_uuid,
                "ifname",
                self.interface,
                timeout=70,
            )
            active = self._nmcli(
                "--terse",
                "--escape",
                "no",
                "--get-values",
                "connection.uuid",
                "connection",
                "show",
                "--active",
            ).decode("utf-8", errors="strict").splitlines()
            return profile.connection_uuid in active
        except (ProvisioningError, UnicodeError):
            return False


def _validate_runtime_object(path: Path, expected_uid: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProvisioningError(
            "invalid_client_runtime", "official Espressif client runtime is incomplete"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not (
        stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
    ):
        _fail("invalid_client_runtime", "official Espressif runtime has an unsafe object")
    if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) & 0o022:
        _fail(
            "invalid_client_runtime",
            "official Espressif runtime must be owner-controlled and not group/other writable",
        )


def validate_official_client_paths(
    tool_dir: Path, protocomm_dir: Path, *, expected_uid: int | None = None
) -> tuple[Path, Path]:
    if expected_uid is None:
        expected_uid = os.geteuid()
    tool_dir = _validate_absolute(tool_dir, "Espressif esp_prov tool directory")
    protocomm_dir = _validate_absolute(
        protocomm_dir, "Espressif protocomm Python directory"
    )
    if (
        protocomm_dir.name != "python"
        or protocomm_dir.parent.name != "protocomm"
        or protocomm_dir.parent.parent.name != "components"
    ):
        _fail(
            "invalid_client_runtime",
            "protocomm Python directory must be under an ESP-IDF components tree",
        )
    idf_root = protocomm_dir.parents[2]
    network_python_dir = tool_dir.parents[1] / "python"
    required = (
        tool_dir,
        tool_dir / "prov",
        tool_dir / "security",
        tool_dir / "transport",
        tool_dir / "esp_prov.py",
        tool_dir / "prov" / "__init__.py",
        tool_dir / "security" / "security1.py",
        tool_dir / "transport" / "transport_http.py",
        protocomm_dir,
        protocomm_dir / "constants_pb2.py",
        protocomm_dir / "sec0_pb2.py",
        protocomm_dir / "sec1_pb2.py",
        protocomm_dir / "sec2_pb2.py",
        protocomm_dir / "session_pb2.py",
        network_python_dir,
        network_python_dir / "network_constants_pb2.py",
        network_python_dir / "network_config_pb2.py",
        network_python_dir / "network_scan_pb2.py",
        network_python_dir / "network_ctrl_pb2.py",
    )
    for path in required:
        _validate_runtime_object(path, expected_uid)
    _validate_runtime_object(idf_root, expected_uid)
    _validate_runtime_object(idf_root / "components", expected_uid)
    _validate_runtime_object(idf_root / "components" / "protocomm", expected_uid)
    return tool_dir, protocomm_dir


def _official_module_name(name: str) -> bool:
    roots = {
        "prov",
        "security",
        "transport",
        "utils",
        "proto",
        "constants_pb2",
        "sec0_pb2",
        "sec1_pb2",
        "sec2_pb2",
        "session_pb2",
        "network_constants_pb2",
        "network_config_pb2",
        "network_scan_pb2",
        "network_ctrl_pb2",
    }
    root = name.split(".", 1)[0]
    return root in roots


def load_official_client(tool_dir: Path, protocomm_dir: Path) -> ModuleType:
    tool_dir, protocomm_dir = validate_official_client_paths(tool_dir, protocomm_dir)
    idf_root = protocomm_dir.parents[2]
    previous_sys_path = list(sys.path)
    previous_idf_path = os.environ.get("IDF_PATH")
    had_idf_path = "IDF_PATH" in os.environ
    previous_modules = {
        name: module for name, module in sys.modules.items() if _official_module_name(name)
    }
    for name in previous_modules:
        sys.modules.pop(name, None)
    sys.path[0:0] = [str(protocomm_dir), str(tool_dir)]
    os.environ["IDF_PATH"] = str(idf_root)
    try:
        spec = importlib.util.spec_from_file_location(
            "_noob_official_esp_prov", tool_dir / "esp_prov.py"
        )
        if spec is None or spec.loader is None:
            _fail("invalid_client_runtime", "official Espressif client could not be loaded")
        module = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(DISCARD_OUTPUT), contextlib.redirect_stderr(
            DISCARD_OUTPUT
        ):
            spec.loader.exec_module(module)
    except ProvisioningError:
        raise
    except Exception as error:
        raise ProvisioningError(
            "invalid_client_runtime", "official Espressif client could not be loaded"
        ) from error
    finally:
        sys.path[:] = previous_sys_path
        if had_idf_path:
            assert previous_idf_path is not None
            os.environ["IDF_PATH"] = previous_idf_path
        else:
            os.environ.pop("IDF_PATH", None)
        for name in tuple(sys.modules):
            if _official_module_name(name):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)
    module.config_throw_except = True
    return module


async def provision_with_official_client(
    client: ModuleType,
    material: CameraMaterial,
    profile: WifiProfile,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
) -> None:
    """Use the official client functions without exposing protected arguments."""

    transport = None
    try:
        with contextlib.redirect_stdout(DISCARD_OUTPUT), contextlib.redirect_stderr(
            DISCARD_OUTPUT
        ):
            transport = await client.get_transport("softap", endpoint)
            if transport is None:
                _fail("camera_unreachable", "camera provisioning endpoint is unavailable")
            version_raw = await client.get_version(transport)
            version = json.loads(version_raw)
            provision = version.get("prov", {}) if isinstance(version, dict) else {}
            if provision.get("sec_ver") != 1:
                _fail("security_mismatch", "camera did not advertise Security 1")
            capabilities = provision.get("cap", [])
            if not isinstance(capabilities, list) or "wifi_prov" not in capabilities:
                _fail("capability_mismatch", "camera did not advertise Wi-Fi provisioning")
            security = client.get_security(
                1, 0, "", "", material.provisioning_pop, False
            )
            if security is None or not await client.establish_session(transport, security):
                _fail("session_failed", "protected camera session could not be established")
            if not await client.send_wifi_config(
                transport, security, profile.ssid, profile.psk
            ):
                _fail("credentials_rejected", "camera rejected the Wi-Fi configuration")
            if not await client.apply_wifi_config(transport, security):
                _fail("apply_rejected", "camera rejected the Wi-Fi apply request")
    except ProvisioningError:
        raise
    except Exception as error:
        raise ProvisioningError(
            "official_client_failed", "official Espressif provisioning did not complete"
        ) from error
    finally:
        if transport is not None:
            try:
                with contextlib.redirect_stdout(DISCARD_OUTPUT), contextlib.redirect_stderr(
                    DISCARD_OUTPUT
                ):
                    await transport.disconnect()
            except Exception:
                pass


def write_result(
    path: Path,
    *,
    status: str,
    state: RunState,
    error_code: str | None = None,
    allowed_root: Path = Path("/run"),
) -> None:
    path = _validate_absolute(path, "result file")
    allowed_root = allowed_root.resolve(strict=True)
    try:
        path.relative_to(allowed_root)
    except ValueError:
        _fail("invalid_result_path", "result file must remain under the runtime directory")
    if not path.parent.is_dir() or path.parent.is_symlink():
        _fail("invalid_result_path", "result parent must be a real directory")
    result = {
        "schema": RESULT_SCHEMA,
        "status": status,
        "stage": state.stage,
        "setup_profile_removed": state.setup_removed,
        "target_profile_restore": state.restore,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if error_code is not None:
        result["error_code"] = error_code
    encoded = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".noob-provision.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _acquire_lock(path: Path):
    path = _validate_absolute(path, "provisioning lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ProvisioningError(
            "already_running", "another camera provisioning run is active"
        ) from error
    return os.fdopen(descriptor, "r+")


async def execute(args: argparse.Namespace, runner: Runner) -> int:
    if os.geteuid() != 0:
        _fail("root_required", "camera provisioning must run as root on the uConsole")
    if not CAMERA_SERVICE.fullmatch(args.camera_service_name):
        _fail("invalid_argument", "camera service name must match NOOB-CAM-XXXXXX")
    if not INTERFACE_NAME.fullmatch(args.interface):
        _fail("invalid_argument", "Wi-Fi interface name is invalid")

    state = RunState()
    error: ProvisioningError | None = None
    profile: WifiProfile | None = None
    network_switch_attempted = False
    manager = NetworkManager(
        runner,
        interface=args.interface,
        keyfile_path=args.keyfile_path,
        expected_uid=0,
    )
    with _acquire_lock(args.lock_path):
        try:
            material = load_camera_material(args.material_file, expected_uid=0)
            profile = read_wifi_profile(runner, args.target_profile, args.interface)
            client = load_official_client(args.esp_prov_tool_dir, args.protocomm_python_dir)
            state.stage = "joining_setup_network"
            manager.prepare(args.camera_service_name, material.provisioning_ap_key)
            network_switch_attempted = True
            manager.activate_setup()
            state.stage = "protected_provisioning"
            await provision_with_official_client(
                client, material, profile, endpoint=args.endpoint
            )
            state.stage = "credentials_applied"
        except ProvisioningError as caught:
            error = caught
        finally:
            state.setup_removed = manager.remove_setup()
            if profile is not None and network_switch_attempted:
                state.restore = "confirmed" if manager.restore(profile) else "failed"
            elif profile is not None:
                state.restore = "not_needed"
            if not state.setup_removed and error is None:
                error = ProvisioningError(
                    "cleanup_failed", "ephemeral setup profile cleanup was not confirmed"
                )
            if state.restore == "failed" and error is None:
                error = ProvisioningError(
                    "restore_failed", "target NetworkManager profile restoration was not confirmed"
                )
            status = "credentials_applied" if error is None else "failed"
            write_result(
                args.result_file,
                status=status,
                state=state,
                error_code=None if error is None else error.code,
            )
    if error is not None:
        raise error
    print(
        "PASS: protected provisioning request applied and original network restored; "
        "live camera acceptance remains a separate proof"
    )
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material-file", type=Path, required=True)
    parser.add_argument("--target-profile", required=True)
    parser.add_argument("--camera-service-name", required=True)
    parser.add_argument("--esp-prov-tool-dir", type=Path, required=True)
    parser.add_argument("--protocomm-python-dir", type=Path, required=True)
    parser.add_argument("--interface", default="wlan0")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, choices=[DEFAULT_ENDPOINT])
    parser.add_argument("--result-file", type=Path, default=DEFAULT_RESULT_PATH)
    parser.add_argument("--keyfile-path", type=Path, default=DEFAULT_KEYFILE_PATH)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(execute(args, SubprocessRunner()))
    except ProvisioningError as error:
        raise SystemExit(f"ERROR [{error.code}]: {error.public_message}") from error


if __name__ == "__main__":
    raise SystemExit(main())
