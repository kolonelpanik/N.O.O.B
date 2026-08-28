from __future__ import annotations

import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "appliance" / "noob_discovery_preflight.py"
SPEC = importlib.util.spec_from_file_location("noob_discovery_preflight", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DiscoveryPreflightTests(unittest.TestCase):
    def test_port_parser_is_canonical_and_bounded(self):
        self.assertEqual(MODULE.parse_port("22"), 22)
        self.assertEqual(MODULE.parse_port("65535"), 65_535)
        for candidate in (None, "", "0", "01", "65536", "22 ", "+22", "tcp"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(MODULE.DiscoveryPreflightError):
                    MODULE.parse_port(candidate)

    def test_non_loopback_listener_matches_ipv4_ipv6_and_wildcard(self):
        report = "\n".join(
            (
                "LISTEN 0 128 127.0.0.1:22 0.0.0.0:*",
                "LISTEN 0 128 0.0.0.0:2222 0.0.0.0:*",
                "LISTEN 0 128 [::]:2200 [::]:*",
                "LISTEN 0 128 *:2022 *:*",
                "LISTEN 0 128 192.168.50.83:2223 0.0.0.0:*",
            )
        )
        self.assertFalse(MODULE.has_non_loopback_listener(report, 22))
        for port in (2222, 2200, 2022, 2223):
            with self.subTest(port=port):
                self.assertTrue(MODULE.has_non_loopback_listener(report, port))

    def test_loopback_multicast_wrong_port_and_malformed_rows_fail_closed(self):
        report = "\n".join(
            (
                "LISTEN 0 128 127.0.0.1:22 0.0.0.0:*",
                "LISTEN 0 128 [::1]:22 [::]:*",
                "LISTEN 0 128 224.0.0.251:22 0.0.0.0:*",
                "LISTEN 0 128 [ff02::fb]:22 [::]:*",
                "not an ss row",
            )
        )
        self.assertFalse(MODULE.has_non_loopback_listener(report, 22))
        self.assertFalse(MODULE.has_non_loopback_listener(report, 2222))


class DiscoveryPackagingTests(unittest.TestCase):
    def test_systemd_unit_has_fixed_bounded_advertisement(self):
        unit = (ROOT / "packaging" / "noob-discovery.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("User=noob", unit)
        self.assertIn("_noob-kvm._tcp ${NOOB_DISCOVERY_SSH_PORT}", unit)
        self.assertIn("ExecStartPre=/usr/bin/python3", unit)
        self.assertIn("EnvironmentFile=/etc/default/noob-discovery", unit)
        self.assertNotIn("EnvironmentFile=/etc/noob/", unit)
        self.assertIn("/opt/noob-discovery/appliance/", unit)
        self.assertIn("Requires=avahi-daemon.service", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("CapabilityBoundingSet=", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertNotIn("AF_NETLINK", unit)
        self.assertNotIn("--ipv4", unit)
        self.assertIn("/usr/bin/avahi-publish-service", unit)
        self.assertNotIn("_noobcam._tcp", unit)

        exec_start = next(
            line for line in unit.splitlines() if line.startswith("ExecStart=")
        )
        arguments = shlex.split(exec_start.removeprefix("ExecStart="))
        service_index = arguments.index("_noob-kvm._tcp")
        self.assertEqual(
            arguments[service_index + 2 :],
            [
                "api=1",
                "product=N.O.O.B.",
                "version=0.2.0",
                "capabilities=target-video,target-hid,ssh-forward",
            ],
        )
        for forbidden in (
            "token",
            "bearer",
            "password",
            "secret",
            "cookie",
            "fingerprint",
            "gateway_url",
            "camera_address",
        ):
            self.assertNotIn(forbidden, exec_start.lower())

    def test_installer_is_syntactically_valid_and_requires_explicit_port(self):
        installer_path = ROOT / "scripts" / "install_uconsole_discovery.sh"
        installer = installer_path.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["sh", "-n", str(installer_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn("--ssh-port PORT", installer)
        self.assertIn("NOOB_DISCOVERY_SSH_PORT=", installer)
        self.assertIn("/etc/default/noob-discovery", installer)
        self.assertNotIn("/etc/noob/discovery", installer)
        self.assertIn("enable --now noob-discovery.service", installer)
        self.assertNotIn("gateway.token", installer)
        self.assertNotIn("local-console.key", installer)
        self.assertNotIn("_noobcam", installer)

    def test_documentation_preserves_manual_fallback_and_camera_separation(self):
        documentation = (ROOT / "docs" / "appliance-discovery.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("manual", documentation.lower())
        self.assertIn("_noobcam._tcp.local", documentation)
        self.assertIn("disable --now noob-discovery.service", documentation)
        self.assertIn(
            "Independent SSH host-key verification",
            " ".join(documentation.split()),
        )
        self.assertIn("bare `fe80::/10`", documentation)
        self.assertIn("prefer RFC1918 IPv4", documentation)


if __name__ == "__main__":
    unittest.main()
