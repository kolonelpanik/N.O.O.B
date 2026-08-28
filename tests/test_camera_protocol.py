import csv
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT / "camera"


class CameraProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(
            (CAMERA / "protocol" / "contract.json").read_text(encoding="utf-8")
        )
        cls.api_source = (CAMERA / "main" / "api_server.cpp").read_text(
            encoding="utf-8"
        )
        cls.camera_source = (CAMERA / "main" / "camera_manager.cpp").read_text(
            encoding="utf-8"
        )
        cls.media_source = (CAMERA / "main" / "media_store.cpp").read_text(
            encoding="utf-8"
        )

    def test_toolchain_and_components_are_exactly_pinned(self):
        manifest = (CAMERA / "main" / "idf_component.yml").read_text(
            encoding="utf-8"
        )
        for version in ("6.0.2", "2.1.7", "1.11.3", "1.2.4", "1.7.19~2"):
            self.assertIn(f'"=={version}"', manifest)
        defaults = (CAMERA / "sdkconfig.defaults").read_text(encoding="utf-8")
        for setting in (
            'CONFIG_IDF_TARGET="esp32"',
            "CONFIG_ESPTOOLPY_FLASHMODE_DIO=y",
            'CONFIG_ESPTOOLPY_FLASHMODE="dio"',
            "CONFIG_ESPTOOLPY_FLASHFREQ_40M=y",
            'CONFIG_ESPTOOLPY_FLASHFREQ="40m"',
            "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y",
            'CONFIG_ESPTOOLPY_FLASHSIZE="4MB"',
            "CONFIG_PARTITION_TABLE_CUSTOM=y",
            'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"',
            "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y",
            "CONFIG_SPIRAM=y",
            "CONFIG_SPIRAM_IGNORE_NOTFOUND=y",
            "CONFIG_SPIRAM_USE_MALLOC=y",
            "CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384",
            "CONFIG_ESP_INT_WDT=y",
            "CONFIG_ESP_TASK_WDT_EN=y",
            "CONFIG_ESP_TASK_WDT_INIT=y",
            "CONFIG_ESP_TASK_WDT_TIMEOUT_S=10",
        ):
            self.assertIn(setting, defaults)
        for protected_key in (
            "CONFIG_NOOB_CAMERA_PROVISIONING_POP=",
            "CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY=",
            "CONFIG_NOOB_CAMERA_API_TOKEN=",
        ):
            self.assertNotIn(protected_key, defaults)
        self.assertIn('set(PROJECT_VER "0.2.0")', (CAMERA / "CMakeLists.txt").read_text())
        self.assertIn('default "0.2.0"', (CAMERA / "main" / "Kconfig.projbuild").read_text())

    def test_partition_contract_is_4mb_dual_ota_without_internal_media(self):
        rows = []
        with (CAMERA / "partitions.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.reader(line for line in handle if not line.startswith("#")):
                if row:
                    rows.append([value.strip() for value in row])
        by_name = {row[0]: row for row in rows}
        self.assertEqual(by_name["ota_0"][4], "1700K")
        self.assertEqual(by_name["ota_1"][4], "1700K")
        self.assertEqual(by_name["nvs"][4], "0x6000")
        forbidden = {"spiffs", "littlefs", "fat"}
        self.assertTrue(forbidden.isdisjoint({row[2].lower() for row in rows}))
        app_main = (CAMERA / "main" / "app_main.cpp").read_text(encoding="utf-8")
        self.assertIn("esp_flash_get_physical_size", app_main)

    def test_only_well_known_document_is_unauthenticated(self):
        auth = self.contract["authentication"]
        self.assertEqual(auth["unauthenticated_paths"], ["/.well-known/noob-camera"])
        self.assertTrue(auth["query_credentials_forbidden"])
        for endpoint in self.contract["endpoints"]:
            if endpoint["path"] != "/.well-known/noob-camera":
                self.assertTrue(endpoint["authenticated"], endpoint)
        self.assertIn('httpd_req_get_hdr_value_len(request, "Authorization")', self.api_source)
        self.assertIn("query_token_forbidden", self.api_source)

    def test_identifier_contract_is_opaque_and_path_free(self):
        identifiers = self.contract["identifiers"]
        self.assertFalse(identifiers["identifiers_are_paths"])
        self.assertRegex("cam_0123456789abcdef", identifiers["device_id_pattern"])
        self.assertRegex("m_" + "a" * 32, identifiers["media_id_pattern"])
        self.assertRegex("j_" + "f" * 32, identifiers["job_id_pattern"])
        self.assertNotRegex("../../etc/passwd", identifiers["media_id_pattern"])
        self.assertIn("valid_media_id(id)", self.api_source)

    def test_camera_baseline_is_diagnostic_first(self):
        state = self.contract["camera_state"]
        self.assertEqual(state["configured_pinmap"], "ai_thinker_candidate")
        self.assertEqual(state["required_sensor_pid"], 0x26)
        self.assertEqual((state["baseline_width"], state["baseline_height"]), (640, 480))
        for evidence in (
            "esp_psram_is_initialized()",
            "sensor->id.PID == OV2640_PID",
            "valid_jpeg(frame)",
            "status_.pinmap_verified",
            "PIXFORMAT_JPEG",
            "FRAMESIZE_VGA",
            "config.fb_count = 2",
            "CAMERA_FB_IN_PSRAM",
            "diagnostic_frame_timeout",
        ):
            self.assertIn(evidence, self.camera_source)

    def test_ai_thinker_candidate_pin_map_matches_official_reference(self):
        expected = {
            "kPinPwdn": 32,
            "kPinReset": -1,
            "kPinXclk": 0,
            "kPinSccbSda": 26,
            "kPinSccbScl": 27,
            "kPinD7": 35,
            "kPinD6": 34,
            "kPinD5": 39,
            "kPinD4": 36,
            "kPinD3": 21,
            "kPinD2": 19,
            "kPinD1": 18,
            "kPinD0": 5,
            "kPinVsync": 25,
            "kPinHref": 23,
            "kPinPclk": 22,
        }
        for name, gpio in expected.items():
            self.assertRegex(
                self.camera_source,
                rf"constexpr int {re.escape(name)} = {gpio};",
            )

    def test_single_capture_arbiter_and_single_upstream_stream(self):
        camera_calls = []
        for source in (CAMERA / "main").glob("*.cpp"):
            text = source.read_text(encoding="utf-8")
            if "esp_camera_fb_get()" in text:
                camera_calls.append(source.name)
        self.assertEqual(camera_calls, ["camera_manager.cpp"])
        self.assertEqual(self.contract["limits"]["upstream_streams"], 1)
        self.assertIn("stream_claimed_", self.api_source)
        self.assertIn("httpd_req_async_handler_begin", self.api_source)
        self.assertIn("httpd_req_async_handler_complete", self.api_source)
        self.assertIn("cJSON_ParseWithLengthOpts", self.api_source)

    def test_storage_is_one_bit_nonformatting_and_atomic(self):
        storage = self.contract["storage"]
        self.assertEqual(storage["width_bits"], 1)
        self.assertFalse(storage["autoformat"])
        self.assertEqual(storage["commit_operation"], "rename")
        self.assertIn("slot.width = 1", self.media_source)
        self.assertIn("mount_config.format_if_mount_failed = false", self.media_source)
        self.assertIn('".partial-" + item.id', self.media_source)
        self.assertIn("rename(partial.c_str(), complete.c_str())", self.media_source)

    def test_clip_bounds_and_cancel_are_contractual(self):
        limits = self.contract["limits"]
        self.assertEqual(limits["clip_duration_ms"]["maximum"], 30000)
        self.assertEqual(limits["clip_fps"]["maximum"], 5)
        self.assertEqual(limits["clip_frames_maximum"], 150)
        endpoints = {(item["method"], item["path"]): item for item in self.contract["endpoints"]}
        self.assertIn(("DELETE", "/api/v1/jobs/{job_id}"), endpoints)
        states = endpoints[("GET", "/api/v1/jobs/{job_id}")]["job_states"]
        self.assertIn("cancelling", states)
        self.assertIn("cancelled", states)
        self.assertIn("cancel_job_id_", self.media_source)

    def test_no_credentials_or_flash_action_are_committed(self):
        kconfig = (CAMERA / "main" / "Kconfig.projbuild").read_text(encoding="utf-8")
        self.assertEqual(kconfig.count('default ""'), 3)
        listed = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--others",
                "--cached",
                "--exclude-standard",
                "--",
                "camera",
            ]
        )
        source_text = "\n".join(
            (ROOT / relative.decode()).read_text(encoding="utf-8", errors="ignore")
            for relative in listed.split(b"\0")
            if relative
        )
        self.assertNotIn("abcd1234", source_text)
        build_script = (CAMERA / "scripts" / "build_source.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("export KCONFIG_REPORT_VERBOSITY=quiet", build_script)
        self.assertIn('REDACTED_IDF="$SCRIPT_DIR/run_redacted_idf.py"', build_script)
        self.assertIn("--sdkconfig sdkconfig --validate-only", build_script)
        self.assertNotIn("idf.py build", build_script)
        self.assertNotRegex(build_script, r"\bidf\.py\s+(?:-p\s+\S+\s+)?flash\b")
        self.assertNotIn("esptool.py write_flash", build_script)


if __name__ == "__main__":
    unittest.main()
