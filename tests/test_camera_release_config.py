from __future__ import annotations

import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "camera" / "scripts" / "configure_release.py"
REDACTOR_SCRIPT = ROOT / "camera" / "scripts" / "run_redacted_idf.py"
SPEC = importlib.util.spec_from_file_location("configure_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
REDACTOR_SPEC = importlib.util.spec_from_file_location("run_redacted_idf", REDACTOR_SCRIPT)
assert REDACTOR_SPEC is not None and REDACTOR_SPEC.loader is not None
REDACTOR = importlib.util.module_from_spec(REDACTOR_SPEC)
sys.modules[REDACTOR_SPEC.name] = REDACTOR
REDACTOR_SPEC.loader.exec_module(REDACTOR)


class CameraReleaseConfigurationTests(unittest.TestCase):
    def test_generated_material_and_pairing_file_are_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            protected = Path(temporary) / "protected"
            protected.mkdir(mode=0o700)
            material_path = protected / "device.json"
            pairing_path = protected / "camera.key"
            MODULE.generate_material(material_path, "camera-test")
            material = MODULE.load_material(material_path)
            MODULE.write_gateway_value(pairing_path, material)

            self.assertEqual(stat.S_IMODE(material_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(pairing_path.stat().st_mode), 0o600)
            self.assertGreaterEqual(len(material.api_token), 32)
            self.assertNotEqual(material.api_token, material.provisioning_pop)
            self.assertNotEqual(material.api_token, material.provisioning_ap_key)

    def test_generation_is_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            protected = Path(temporary) / "protected"
            protected.mkdir(mode=0o700)
            material_path = protected / "device.json"
            MODULE.generate_material(material_path, "camera-test")
            with self.assertRaises(FileExistsError):
                MODULE.generate_material(material_path, "camera-test")

    def test_loader_rejects_group_readable_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            protected = Path(temporary) / "protected"
            protected.mkdir(mode=0o700)
            material_path = protected / "device.json"
            MODULE.generate_material(material_path, "camera-test")
            material_path.chmod(0o640)
            with self.assertRaises(MODULE.ConfigurationError):
                MODULE.load_material(material_path)

    def test_repository_material_path_is_rejected(self) -> None:
        with self.assertRaises(MODULE.ConfigurationError):
            MODULE._require_external_absolute(
                ROOT / "camera" / "secrets" / "device.json", "material file"
            )

    def test_cli_prints_no_generated_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            camera_root = root / "camera"
            camera_root.mkdir(parents=True)
            (camera_root / "sdkconfig.defaults").write_text(
                (ROOT / "camera" / "sdkconfig.defaults").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            protected = Path(temporary) / "protected"
            protected.mkdir(mode=0o700)
            material_path = protected / "device.json"
            sdkconfig = camera_root / "sdkconfig"
            sdkconfig.write_text("CONFIG_LOCAL_MENUCONFIG_DRIFT=y\n", encoding="utf-8")
            arguments = Namespace(
                material_file=material_path,
                device_label="camera-test",
                generate_if_missing=True,
                sdkconfig=sdkconfig,
                gateway_pairing_file=None,
                validate_only=False,
            )
            output = io.StringIO()
            with (
                mock.patch.object(MODULE, "CAMERA_ROOT", camera_root),
                mock.patch.object(MODULE, "REPOSITORY_ROOT", root),
                mock.patch.object(MODULE, "parse_args", return_value=arguments),
                redirect_stdout(output),
            ):
                self.assertEqual(MODULE.main(), 0)
                payload = json.loads(material_path.read_text())
                for key in ("provisioning_pop", "provisioning_ap_key", "api_token"):
                    self.assertNotIn(payload[key], output.getvalue())
                self.assertIn("no protected values were printed", output.getvalue())
                self.assertEqual(stat.S_IMODE(sdkconfig.stat().st_mode), 0o600)
                rendered = sdkconfig.read_text(encoding="utf-8")
                self.assertNotIn("CONFIG_LOCAL_MENUCONFIG_DRIFT", rendered)
                for setting in MODULE.REQUIRED_RELEASE_SETTINGS:
                    self.assertIn(f"{setting}\n", rendered)

    def test_validation_rejects_missing_release_contract_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repository"
            camera_root = root / "camera"
            camera_root.mkdir(parents=True)
            sdkconfig = camera_root / "sdkconfig"
            sdkconfig.write_text(
                "\n".join(MODULE.REQUIRED_RELEASE_SETTINGS[1:]) + "\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(MODULE, "CAMERA_ROOT", camera_root),
                self.assertRaisesRegex(
                    MODULE.ConfigurationError,
                    "deterministic release contract",
                ),
            ):
                MODULE.validate_release_sdkconfig(sdkconfig)

    def test_idf_output_filter_redacts_every_release_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sdkconfig = Path(temporary) / "sdkconfig"
            sdkconfig.write_text(
                '\n'.join(
                    (
                        'CONFIG_NOOB_CAMERA_PROVISIONING_POP="example_pop_value"',
                        'CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY="example_ap_key"',
                        'CONFIG_NOOB_CAMERA_API_TOKEN="example_camera_api_value_1234567890"',
                    )
                )
                + '\n'
            )
            values = REDACTOR.read_release_values(sdkconfig)
            raw = f"before {values[0]} middle {values[1]} after {values[2]}\n"
            filtered = REDACTOR.redact_line(raw, values)
            self.assertNotIn(values[0], filtered)
            self.assertNotIn(values[1], filtered)
            self.assertNotIn(values[2], filtered)
            self.assertEqual(filtered.count(REDACTOR.REDACTION), 3)


if __name__ == "__main__":
    unittest.main()
