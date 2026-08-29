from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UConsoleNetworkResilienceTests(unittest.TestCase):
    def test_networkd_template_is_exact_dynamic_and_dns_neutral(self):
        network = (
            ROOT / "packaging" / "noob-usb-recovery.network.in"
        ).read_text(encoding="utf-8")
        self.assertIn("Name=__NOOB_USB_INTERFACE__", network)
        self.assertIn("DHCP=ipv4", network)
        self.assertIn("IPv6AcceptRA=yes", network)
        self.assertIn("LinkLocalAddressing=ipv6", network)
        self.assertIn("RequiredForOnline=no", network)
        self.assertIn("RouteMetric=100", network)
        self.assertGreaterEqual(network.count("UseDNS=no"), 2)
        self.assertIn("DNSDefaultRoute=no", network)
        directives = tuple(
            line.strip() for line in network.splitlines() if not line.startswith("#")
        )
        for forbidden in ("Address=", "Gateway=", "DNS="):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(line.startswith(forbidden) for line in directives)
                )
        self.assertNotIn("192.168.", network)

    def test_networkmanager_template_unmanages_only_rendered_interface(self):
        config = (
            ROOT / "packaging" / "noob-usb-recovery.networkmanager.conf.in"
        ).read_text(encoding="utf-8")
        self.assertIn("[device-noob-usb-recovery]", config)
        self.assertIn(
            "match-device=interface-name:__NOOB_USB_INTERFACE__", config
        )
        self.assertIn("managed=0", config)
        self.assertNotIn("unmanaged-devices=", config)
        self.assertNotIn("match-device=interface-name:wlan0", config)

    def test_installer_is_syntactically_valid_and_usb0_bounded(self):
        path = ROOT / "scripts" / "install_uconsole_network_resilience.sh"
        installer = path.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["sh", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn("--interface usb0", installer)
        self.assertIn('[ "$INTERFACE" != usb0 ]', installer)
        self.assertIn("systemd-networkd.service", installer)
        self.assertIn("nmcli general reload conf", installer)
        self.assertIn("GENERAL.NM-MANAGED", installer)
        self.assertIn("networkd_was_enabled=false", installer)
        self.assertIn(
            "systemctl disable systemd-networkd-wait-online.service", installer
        )
        self.assertIn('networkctl reconfigure "$INTERFACE"', installer)
        self.assertIn("refusing to replace an unmanaged destination", installer)
        self.assertNotIn("systemctl restart NetworkManager", installer)
        self.assertNotIn("ip address add", installer)

        nm_reload = installer.index("nmcli general reload conf")
        nm_proof = installer.index("GENERAL.NM-MANAGED")
        networkd_start = installer.index(
            "systemctl enable --now systemd-networkd.service"
        )
        wait_online_disable = installer.index(
            "systemctl disable systemd-networkd-wait-online.service"
        )
        self.assertLess(nm_reload, nm_proof)
        self.assertLess(nm_proof, networkd_start)
        self.assertLess(networkd_start, wait_online_disable)

    def test_documentation_has_proof_and_scoped_rollback(self):
        documentation = (
            ROOT / "docs" / "appliance-network-resilience.md"
        ).read_text(encoding="utf-8")
        self.assertIn("metric **100**", documentation)
        self.assertIn("same IPv4 subnet", documentation)
        self.assertIn("ip route get <operator-ip> from <usb0-ip>", documentation)
        self.assertIn("avahi-browse", documentation)
        self.assertIn("NetworkManager is stopped", documentation)
        self.assertIn("systemctl disable --now systemd-networkd.service", documentation)
        self.assertIn("do **not** disable that service", documentation)


if __name__ == "__main__":
    unittest.main()
