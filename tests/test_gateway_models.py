import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

from noob_gateway.config import ConfigError, load_config  # noqa: E402
from noob_gateway.__main__ import SHUTDOWN_TIMEOUT_SECONDS, main  # noqa: E402
from noob_gateway.models import InputValidationError, validate_input_command  # noqa: E402


class GatewayModelTests(unittest.TestCase):
    def test_type_is_ascii_bounded_and_copied(self):
        source = {"op": "type", "text": "hello\n", "interval_ms": 5}
        result = validate_input_command(source, max_type_chars=32)
        self.assertEqual(result, source)
        self.assertIsNot(result, source)

    def test_unknown_fields_and_bool_integers_are_rejected(self):
        with self.assertRaisesRegex(InputValidationError, "bad_field"):
            validate_input_command({"op": "ping", "extra": True})
        with self.assertRaisesRegex(InputValidationError, "bad_range"):
            validate_input_command({"op": "mouse_move", "dx": True, "dy": 0, "wheel": 0})

    def test_non_ascii_and_unknown_keys_are_rejected(self):
        with self.assertRaisesRegex(InputValidationError, "bad_range"):
            validate_input_command({"op": "type", "text": "snowman \u2603", "interval_ms": 0})
        with self.assertRaisesRegex(InputValidationError, "bad_key"):
            validate_input_command({"op": "key", "event": "down", "key": "POWER"})

    def test_exact_phase_four_action_examples_are_normalized(self):
        type_source = {"action": "type", "text": "ls -la\n"}
        combo_source = {"action": "combo", "keys": ["GUI", "SPACE"]}

        self.assertEqual(
            validate_input_command(type_source),
            {"op": "type", "text": "ls -la\n", "interval_ms": 0},
        )
        self.assertEqual(
            validate_input_command(combo_source),
            {
                "op": "combo",
                "keys": ["LEFT_GUI", "SPACE"],
                "hold_ms": 50,
            },
        )
        self.assertEqual(type_source, {"action": "type", "text": "ls -la\n"})
        self.assertEqual(combo_source, {"action": "combo", "keys": ["GUI", "SPACE"]})

    def test_action_compatibility_remains_strict_and_bounded(self):
        with self.assertRaisesRegex(InputValidationError, "bad_field"):
            validate_input_command({"action": "ping", "op": "ping"})
        with self.assertRaisesRegex(InputValidationError, "bad_field"):
            validate_input_command(
                {"action": "type", "text": "ok", "unexpected": True}
            )
        with self.assertRaisesRegex(InputValidationError, "bad_range"):
            validate_input_command(
                {"action": "type", "text": "x" * 33}, max_type_chars=32
            )
        with self.assertRaisesRegex(InputValidationError, "bad_key"):
            validate_input_command(
                {"action": "combo", "keys": ["GUI", "LEFT_GUI"]}
            )

    def test_gui_alias_preserves_explicit_side_specific_keys(self):
        self.assertEqual(
            validate_input_command(
                {"op": "combo", "keys": ["GUI", "RIGHT_GUI"], "hold_ms": 20}
            )["keys"],
            ["LEFT_GUI", "RIGHT_GUI"],
        )


class GatewayConfigTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "noob.toml"
            path.write_text(text, encoding="utf-8")
            return load_config(path)

    def test_defaults_are_loopback_only(self):
        config = self._load("")
        self.assertEqual(config.server.host, "127.0.0.1")
        self.assertFalse(config.server.allow_non_loopback)

    def test_non_loopback_requires_explicit_gate(self):
        with self.assertRaisesRegex(ConfigError, "allow_non_loopback"):
            self._load('[server]\nhost = "192.0.2.83"\n')
        config = self._load(
            '[server]\nhost = "192.0.2.83"\nallow_non_loopback = true\n'
        )
        self.assertTrue(config.server.allow_non_loopback)

    def test_unknown_config_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "unknown server keys"):
            self._load("[server]\nmagic = true\n")

    def test_local_console_credential_is_optional_and_distinct(self):
        config = self._load(
            '[auth]\nlocal_token_file = "/etc/noob/local-console.key"\n'
        )
        self.assertEqual(
            config.auth.local_token_file, "/etc/noob/local-console.key"
        )
        with self.assertRaisesRegex(ConfigError, "distinct file"):
            self._load(
                '[auth]\ntoken_file = "/etc/noob/shared.key"\n'
                'local_token_file = "/etc/noob/shared.key"\n'
            )

    def test_local_input_is_fail_closed_and_requires_stable_evdev_paths(self):
        config = self._load("")
        self.assertFalse(config.local_input.enabled)
        self.assertEqual(
            config.local_input.emergency_chord,
            ("LEFT_CONTROL", "LEFT_ALT", "ESCAPE"),
        )

        with self.assertRaisesRegex(ConfigError, "by-id or /dev/input/by-path"):
            self._load(
                '[local_input]\nkeyboard_device = "/dev/input/event4"\n'
            )

    def test_local_input_config_is_strict_and_idle_precedes_lease_expiry(self):
        config = self._load(
            """
[limits]
lease_ttl_seconds = 5.0

[local_input]
enabled = true
keyboard_device = "/dev/input/by-id/uconsole-event-kbd"
pointer_device = "/dev/input/by-path/uconsole-event-mouse"
long_press_ms = 700
lease_idle_ms = 900
reconnect_ms = 500
emergency_chord = ["LEFT_CONTROL", "LEFT_ALT", "ESCAPE"]
"""
        )
        self.assertTrue(config.local_input.enabled)
        self.assertEqual(config.local_input.long_press_ms, 700)

        with self.assertRaisesRegex(ConfigError, "supported keys"):
            self._load(
                '[local_input]\nemergency_chord = ["LEFT_CONTROL", "POWER"]\n'
            )
        with self.assertRaisesRegex(ConfigError, "shorter than the control lease"):
            self._load(
                """
[limits]
lease_ttl_seconds = 1.0
[local_input]
lease_idle_ms = 1000
"""
            )


class GatewayMainTests(unittest.TestCase):
    def test_aiohttp_shutdown_timeout_fits_systemd_stop_window(self):
        fake_config = mock.Mock()
        fake_config.server.host = "127.0.0.1"
        fake_config.server.port = 8765
        fake_app = object()
        with (
            mock.patch("sys.argv", ["noob-gateway", "--config", "/tmp/noob.toml"]),
            mock.patch("noob_gateway.__main__.load_config", return_value=fake_config),
            mock.patch("noob_gateway.__main__.create_app", return_value=fake_app),
            mock.patch("noob_gateway.__main__.web.run_app") as run_app,
        ):
            main()

        self.assertEqual(SHUTDOWN_TIMEOUT_SECONDS, 2.0)
        self.assertLess(SHUTDOWN_TIMEOUT_SECONDS, 5.0)
        run_app.assert_called_once_with(
            fake_app,
            host="127.0.0.1",
            port=8765,
            access_log=None,
            shutdown_timeout=SHUTDOWN_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
