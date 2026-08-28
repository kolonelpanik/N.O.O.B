from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import (  # noqa: E402
    ConfigError,
    VIDEO_FRAME_HARD_CEILING,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


class VideoProfileConfigTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "noob.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_default_profiles_expose_only_explicitly_validated_mode(self):
        video = self._load("").video
        self.assertEqual(video.default_mode, "720p20")
        self.assertEqual(
            [profile.mode_id for profile in video.profiles if profile.validated],
            ["720p20"],
        )
        self.assertEqual(
            {profile.mode_id for profile in video.profiles},
            {
                "720p20",
                "720p30",
                "1080p30",
                "1200p30",
                "1440p30",
                "1600p30",
            },
        )
        self.assertLessEqual(video.max_frame_bytes, VIDEO_FRAME_HARD_CEILING)

    def test_shipped_configs_load_and_do_not_advertise_4k(self):
        expected_validated = {
            "config/noob.toml.example": ["720p20"],
            "config/noob.uconsole.toml": [
                "720p20",
                "720p30",
                "1080p30",
                "1440p30",
                "1200p30",
                "1600p30",
            ],
        }
        for relative, expected in expected_validated.items():
            with self.subTest(relative=relative):
                video = load_config(ROOT / relative).video
                self.assertEqual(video.default_mode, "720p20")
                self.assertEqual(
                    [item.mode_id for item in video.profiles if item.validated],
                    expected,
                )
                self.assertFalse(
                    any(
                        item.width == 3840
                        or item.height == 2160
                        or "4k" in item.label.lower()
                        for item in video.profiles
                    )
                )

    def test_pre_profile_fixed_mode_config_is_preserved_exactly(self):
        video = self._load(
            """
[video]
width = 1280
height = 720
fps = 15
max_frame_bytes = 2097152
"""
        ).video
        self.assertEqual(video.default_mode, "legacy-1280x720-15")
        self.assertEqual((video.width, video.height, video.fps), (1280, 720, 15))
        self.assertEqual(len(video.profiles), 1)
        self.assertTrue(video.default_profile.validated)

    def test_legacy_aliases_are_all_or_nothing_and_must_match_new_default(self):
        with self.assertRaisesRegex(ConfigError, "supplied together"):
            self._load("[video]\nwidth = 1280\nheight = 720\n")
        with self.assertRaisesRegex(ConfigError, "must match"):
            self._load(
                """
[video]
default_mode = "720p20"
width = 1280
height = 720
fps = 15
"""
            )

    def test_profile_tables_are_strict_mjpg_and_bounded(self):
        base = """
[video]
default_mode = "safe-mode"
max_frame_bytes = 16777216

[[video.profiles]]
mode_id = "safe-mode"
label = "Safe mode"
width = 1280
height = 720
fps = 20
pixel_format = "{pixel_format}"
max_frame_bytes = {frame_bytes}
validated = {validated}
"""
        config = self._load(
            base.format(
                pixel_format="MJPG", frame_bytes=1843200, validated="true"
            )
        )
        self.assertEqual(config.video.default_profile.mode_id, "safe-mode")
        with self.assertRaisesRegex(ConfigError, "must be MJPG"):
            self._load(
                base.format(
                    pixel_format="YUYV", frame_bytes=1843200, validated="true"
                )
            )
        with self.assertRaisesRegex(ConfigError, "allowed range"):
            self._load(
                base.format(
                    pixel_format="MJPG",
                    frame_bytes=VIDEO_FRAME_HARD_CEILING + 1,
                    validated="true",
                )
            )
        with self.assertRaisesRegex(ConfigError, "validated profile"):
            self._load(
                base.format(
                    pixel_format="MJPG", frame_bytes=1843200, validated="false"
                )
            )

    def test_profile_ids_use_canonical_hyphen_only_64_character_grammar(self):
        prefix = "m" + "a" * 62 + "-"
        self.assertEqual(len(prefix), 64)
        text = f"""
[video]
default_mode = "{prefix}"

[[video.profiles]]
mode_id = "{prefix}"
label = "Boundary ID"
width = 1280
height = 720
fps = 20
max_frame_bytes = 1843200
validated = true
"""
        self.assertEqual(self._load(text).video.default_mode, prefix)
        for invalid in ("has_underscore", "-starts-with-hyphen", "m" + "a" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ConfigError, "mode_id is invalid"):
                    self._load(text.replace(prefix, invalid))

    def test_environment_camera_defaults_disabled_and_shipped_configs_are_safe(self):
        self.assertFalse(self._load("").environment_camera.enabled)
        for relative in ("config/noob.toml.example", "config/noob.uconsole.toml"):
            with self.subTest(relative=relative):
                camera = load_config(ROOT / relative).environment_camera
                self.assertFalse(camera.enabled)
                self.assertIsNone(camera.host)
                self.assertIsNone(camera.token_file)
                self.assertLessEqual(camera.max_clip_seconds * camera.max_clip_fps, 150)

    def test_environment_camera_accepts_only_fixed_private_ip_and_distinct_token(self):
        config = self._load(
            """
[environment_camera]
enabled = true
host = "192.168.50.84"
expected_device_id = "cam_0123456789abcdef"
port = 8080
token_file = "/etc/noob/environment-camera.key"
"""
        )
        self.assertTrue(config.environment_camera.enabled)
        self.assertEqual(config.environment_camera.host, "192.168.50.84")
        self.assertEqual(
            config.environment_camera.expected_device_id,
            "cam_0123456789abcdef",
        )
        self.assertEqual(config.environment_camera.port, 8080)

        for host in (
            "camera.local",
            "http://192.168.50.84",
            "127.0.0.1",
            "169.254.1.2",
            "8.8.8.8",
            "224.0.0.1",
            "2001:4860:4860::8888",
        ):
            with self.subTest(host=host):
                with self.assertRaises(ConfigError):
                    self._load(
                        f"""
[environment_camera]
enabled = true
host = "{host}"
expected_device_id = "cam_0123456789abcdef"
token_file = "/etc/noob/environment-camera.key"
"""
                    )

    def test_enabled_environment_camera_requires_address_and_credential(self):
        with self.assertRaisesRegex(ConfigError, "requires environment_camera.host"):
            self._load(
                """
[environment_camera]
enabled = true
token_file = "/etc/noob/environment-camera.key"
expected_device_id = "cam_0123456789abcdef"
"""
            )
        with self.assertRaisesRegex(ConfigError, "requires environment_camera.token_file"):
            self._load(
                """
[environment_camera]
enabled = true
host = "10.20.30.40"
expected_device_id = "cam_0123456789abcdef"
"""
            )
        with self.assertRaisesRegex(
            ConfigError, "requires environment_camera.expected_device_id"
        ):
            self._load(
                """
[environment_camera]
enabled = true
host = "10.20.30.40"
token_file = "/etc/noob/environment-camera.key"
"""
            )
        with self.assertRaisesRegex(ConfigError, "distinct from gateway credentials"):
            self._load(
                """
[environment_camera]
enabled = true
host = "172.16.1.20"
expected_device_id = "cam_0123456789abcdef"
token_file = "/etc/noob/auth.key"
"""
            )

        for device_id in (
            "cam_0123456789abcde",
            "cam_0123456789abcdef0",
            "cam_0123456789abcdeF",
            "camera_0123456789abcdef",
        ):
            with self.subTest(device_id=device_id):
                with self.assertRaisesRegex(ConfigError, "must match"):
                    self._load(
                        f"""
[environment_camera]
enabled = false
expected_device_id = "{device_id}"
"""
                    )

    def test_environment_camera_config_is_strict_and_bounded(self):
        with self.assertRaisesRegex(ConfigError, "unknown environment_camera keys"):
            self._load(
                """
[environment_camera]
enabled = false
upstream_url = "http://192.168.50.84/"
"""
            )
        for field, value in (
            ("max_clients", 9),
            ("max_page_size", 51),
            ("max_clip_seconds", 31),
            ("max_clip_fps", 6),
            ("max_metadata_bytes", 512),
            ("max_media_bytes", 134217729),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ConfigError, "allowed range"):
                    self._load(
                        f"""
[environment_camera]
enabled = false
{field} = {value}
"""
                    )
        with self.assertRaisesRegex(ConfigError, "must cover max_frame_bytes"):
            self._load(
                """
[environment_camera]
enabled = false
max_frame_bytes = 2097152
max_media_bytes = 1048576
"""
            )


if __name__ == "__main__":
    unittest.main()
