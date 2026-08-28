from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "camera"
    / "scripts"
    / "provision_from_uconsole.py"
)
SPEC = importlib.util.spec_from_file_location("camera_uconsole_provision", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeTransport:
    def __init__(self) -> None:
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeOfficialClient:
    def __init__(self, secret_marker: str | None = None) -> None:
        self.transport = FakeTransport()
        self.secret_marker = secret_marker
        self.seen: tuple[str, str, str] | None = None

    async def get_transport(self, mode: str, endpoint: str):
        self.mode = mode
        self.endpoint = endpoint
        return self.transport

    async def get_version(self, transport: FakeTransport) -> str:
        if self.secret_marker:
            raise RuntimeError(f"vendor failure accidentally included {self.secret_marker}")
        return json.dumps({"prov": {"sec_ver": 1, "cap": ["wifi_prov"]}})

    def get_security(self, *args):
        self.pop = args[4]
        return object()

    async def establish_session(self, transport: FakeTransport, security: object) -> bool:
        return True

    async def send_wifi_config(
        self, transport: FakeTransport, security: object, ssid: str, psk: str
    ) -> bool:
        self.seen = (self.pop, ssid, psk)
        return True

    async def apply_wifi_config(self, transport: FakeTransport, security: object) -> bool:
        return True


class CollisionRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout=30, allow_failure=False) -> bytes:
        self.calls.append(tuple(argv))
        return b"foreign-profile\n"


class ProfileRunner:
    def __init__(self, connection_uuid: str) -> None:
        self.connection_uuid = connection_uuid
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout=30, allow_failure=False) -> bytes:
        call = tuple(argv)
        self.calls.append(call)
        field = argv[argv.index("--get-values") + 1]
        values = {
            "connection.uuid": self.connection_uuid,
            "connection.type": "802-11-wireless",
            "802-11-wireless.ssid": "Trusted-LAN",
            "802-11-wireless-security.psk": "target_psk_marker_123456",
            "GENERAL.CON-UUID": self.connection_uuid,
        }
        return f"{values[field]}\n".encode()


class CameraUconsoleProvisionTests(unittest.TestCase):
    def _write_material(self, directory: Path, *, mode: int = 0o600) -> tuple[Path, dict]:
        payload = {
            "schema": MODULE.MATERIAL_SCHEMA,
            "device_label": "noob-cam-test",
            "created_at": "2026-08-28T00:00:00+00:00",
            "provisioning_pop": "pop_marker_123456",
            "provisioning_ap_key": "setup_key_marker_123456",
            "api_token": "api_token_marker_12345678901234567890",
        }
        path = directory / "camera.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)
        return path, payload

    def test_material_requires_owner_only_mode_and_loads_only_needed_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, payload = self._write_material(Path(temporary))
            material = MODULE.load_camera_material(path, expected_uid=os.getuid())
            self.assertEqual(material.provisioning_pop, payload["provisioning_pop"])
            self.assertEqual(
                material.provisioning_ap_key, payload["provisioning_ap_key"]
            )
            self.assertFalse(hasattr(material, "api_token"))

            path.chmod(0o640)
            with self.assertRaisesRegex(MODULE.ProvisioningError, "0400 or 0600"):
                MODULE.load_camera_material(path, expected_uid=os.getuid())

    def test_wifi_profile_validation_rejects_controls_and_unbounded_psk(self) -> None:
        valid = MODULE.validate_wifi_profile(
            "66979764-0367-49ab-9b2d-eaa0887a7244", "Trusted-LAN", "safe-password"
        )
        self.assertEqual(valid.ssid, "Trusted-LAN")
        with self.assertRaises(MODULE.ProvisioningError):
            MODULE.validate_wifi_profile(valid.connection_uuid, "bad\nssid", "safe-password")
        with self.assertRaises(MODULE.ProvisioningError):
            MODULE.validate_wifi_profile(valid.connection_uuid, "Trusted-LAN", "short")

    def test_profile_read_checks_actual_active_uuid_on_selected_interface(self) -> None:
        connection_uuid = "66979764-0367-49ab-9b2d-eaa0887a7244"
        runner = ProfileRunner(connection_uuid)
        profile = MODULE.read_wifi_profile(runner, "Trusted-LAN", "wlan0")
        self.assertEqual(profile.connection_uuid, connection_uuid)
        self.assertIn(
            (
                "/usr/bin/nmcli",
                "--terse",
                "--escape",
                "no",
                "--get-values",
                "GENERAL.CON-UUID",
                "device",
                "show",
                "wlan0",
            ),
            runner.calls,
        )

    def test_setup_keyfile_is_bounded_and_has_no_target_credentials(self) -> None:
        rendered = MODULE.render_setup_keyfile(
            "NOOB-CAM-89418C", "temporary_setup_key", "wlan0"
        ).decode()
        self.assertIn("id=noob-camera-provision-ephemeral", rendered)
        self.assertIn("ssid=NOOB-CAM-89418C", rendered)
        self.assertIn("psk=temporary_setup_key", rendered)
        self.assertIn("autoconnect=false", rendered)
        self.assertNotIn("Trusted-LAN", rendered)
        with self.assertRaises(MODULE.ProvisioningError):
            MODULE.render_setup_keyfile("NOOB-CAM-anything", "temporary_setup_key", "wlan0")

    def test_reserved_uuid_collision_never_authorizes_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = CollisionRunner()
            manager = MODULE.NetworkManager(
                runner,
                interface="wlan0",
                keyfile_path=Path(temporary) / "ephemeral.nmconnection",
                expected_uid=os.getuid(),
            )
            with self.assertRaisesRegex(MODULE.ProvisioningError, "UUID is already in use"):
                manager.prepare("NOOB-CAM-89418C", "temporary_setup_key")
            self.assertFalse(manager.cleanup_authorized)
            self.assertTrue(manager.remove_setup())
            self.assertEqual(len(runner.calls), 1)

    def test_official_client_receives_values_in_process_and_disconnects(self) -> None:
        client = FakeOfficialClient()
        material = MODULE.CameraMaterial("pop_marker_123456", "setup_marker_123456")
        profile = MODULE.WifiProfile(
            "66979764-0367-49ab-9b2d-eaa0887a7244",
            "Trusted-LAN",
            "target_psk_marker_123456",
        )
        asyncio.run(MODULE.provision_with_official_client(client, material, profile))
        self.assertEqual(
            client.seen,
            (material.provisioning_pop, profile.ssid, profile.psk),
        )
        self.assertTrue(client.transport.disconnected)

    def test_vendor_exception_is_mapped_without_secret_text(self) -> None:
        marker = "DO_NOT_LEAK_THIS_SECRET"
        client = FakeOfficialClient(secret_marker=marker)
        material = MODULE.CameraMaterial(marker, "setup_marker_123456")
        profile = MODULE.WifiProfile(
            "66979764-0367-49ab-9b2d-eaa0887a7244", "Trusted-LAN", marker
        )
        with self.assertRaises(MODULE.ProvisioningError) as caught:
            asyncio.run(MODULE.provision_with_official_client(client, material, profile))
        public = f"{caught.exception.code} {caught.exception.public_message}"
        self.assertNotIn(marker, public)
        self.assertEqual(caught.exception.code, "official_client_failed")
        self.assertTrue(client.transport.disconnected)

    def test_official_runtime_paths_must_be_owner_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            component = root / "network_provisioning"
            tool = component / "tool" / "esp_prov"
            network_python = component / "python"
            idf_root = root / "esp-idf"
            protocomm = idf_root / "components" / "protocomm" / "python"
            for directory in (
                component,
                component / "tool",
                tool,
                tool / "prov",
                tool / "security",
                tool / "transport",
                network_python,
                idf_root,
                idf_root / "components",
                idf_root / "components" / "protocomm",
                protocomm,
            ):
                directory.mkdir(exist_ok=True, parents=True)
                directory.chmod(0o755)
            for file in (
                tool / "esp_prov.py",
                tool / "prov" / "__init__.py",
                tool / "security" / "security1.py",
                tool / "transport" / "transport_http.py",
                protocomm / "constants_pb2.py",
                protocomm / "sec0_pb2.py",
                protocomm / "sec1_pb2.py",
                protocomm / "sec2_pb2.py",
                protocomm / "session_pb2.py",
                network_python / "network_constants_pb2.py",
                network_python / "network_config_pb2.py",
                network_python / "network_scan_pb2.py",
                network_python / "network_ctrl_pb2.py",
            ):
                file.write_text("# test fixture\n", encoding="utf-8")
                file.chmod(0o644)

            self.assertEqual(
                MODULE.validate_official_client_paths(
                    tool, protocomm, expected_uid=os.getuid()
                ),
                (tool.resolve(), protocomm.resolve()),
            )
            (tool / "esp_prov.py").chmod(0o666)
            with self.assertRaisesRegex(MODULE.ProvisioningError, "owner-controlled"):
                MODULE.validate_official_client_paths(
                    tool, protocomm, expected_uid=os.getuid()
                )

    @unittest.skipUnless(
        os.environ.get("NOOB_TEST_ESP_PROV_TOOL_DIR")
        and os.environ.get("NOOB_TEST_PROTOCOMM_PYTHON_DIR"),
        "real pinned Espressif runtime paths were not supplied",
    )
    def test_real_pinned_official_client_import_restores_environment(self) -> None:
        tool = Path(os.environ["NOOB_TEST_ESP_PROV_TOOL_DIR"])
        protocomm = Path(os.environ["NOOB_TEST_PROTOCOMM_PYTHON_DIR"])
        sentinel = "prior-idf-path-sentinel"
        previous = os.environ.get("IDF_PATH")
        previous_sys_path = list(sys.path)
        previous_utils = sys.modules.get("utils")
        sentinel_utils = SimpleNamespace(marker="ambient-utils-sentinel")
        os.environ["IDF_PATH"] = sentinel
        sys.modules["utils"] = sentinel_utils
        try:
            client = MODULE.load_official_client(tool, protocomm)
            self.assertTrue(callable(client.get_transport))
            self.assertTrue(callable(client.get_security))
            self.assertTrue(callable(client.establish_session))
            self.assertEqual(os.environ.get("IDF_PATH"), sentinel)
            self.assertEqual(sys.path, previous_sys_path)
            self.assertIs(sys.modules.get("utils"), sentinel_utils)
        finally:
            if previous is None:
                os.environ.pop("IDF_PATH", None)
            else:
                os.environ["IDF_PATH"] = previous
            if previous_utils is None:
                sys.modules.pop("utils", None)
            else:
                sys.modules["utils"] = previous_utils

    def test_result_is_owner_only_bounded_and_contains_no_protected_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / "result.json"
            state = MODULE.RunState(
                stage="credentials_applied", setup_removed=True, restore="confirmed"
            )
            MODULE.write_result(
                result,
                status="credentials_applied",
                state=state,
                allowed_root=root,
            )
            text = result.read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertEqual(payload["schema"], MODULE.RESULT_SCHEMA)
            self.assertEqual(payload["status"], "credentials_applied")
            self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)
            for marker in (
                "Trusted-LAN",
                "pop_marker",
                "setup_key_marker",
                "target_psk_marker",
                "api_token_marker",
            ):
                self.assertNotIn(marker, text)

            outside = root.parent / "outside-result.json"
            with self.assertRaises(MODULE.ProvisioningError):
                MODULE.write_result(
                    outside,
                    status="failed",
                    state=state,
                    error_code="test",
                    allowed_root=root,
                )


if __name__ == "__main__":
    unittest.main()
