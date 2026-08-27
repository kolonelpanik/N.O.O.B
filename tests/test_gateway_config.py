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


if __name__ == "__main__":
    unittest.main()
