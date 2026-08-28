#!/usr/bin/env python3
"""Fail-closed preflight for the N.O.O.B. SSH mDNS advertisement.

The advertiser must never claim an SSH port that is not listening on a
non-loopback address.  This helper deliberately reads only the configured port
and the kernel's listening-socket table; it does not read keys, tokens, or SSH
configuration files.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys


PORT_PATTERN = re.compile(r"[1-9][0-9]{0,4}\Z")
SS_EXECUTABLE = "/usr/bin/ss"


class DiscoveryPreflightError(ValueError):
    """The configured advertisement does not map to a usable SSH listener."""


def parse_port(raw: str | None) -> int:
    if raw is None or PORT_PATTERN.fullmatch(raw) is None:
        raise DiscoveryPreflightError("invalid discovery SSH port")
    port = int(raw, 10)
    if port > 65_535:
        raise DiscoveryPreflightError("invalid discovery SSH port")
    return port


def split_listener_endpoint(endpoint: str) -> tuple[str, int] | None:
    value = endpoint.strip()
    if not value or ":" not in value:
        return None
    if value.startswith("["):
        closing = value.rfind("]:")
        if closing < 0:
            return None
        host = value[1:closing]
        raw_port = value[closing + 2 :]
    else:
        host, raw_port = value.rsplit(":", 1)
    if not raw_port.isascii() or not raw_port.isdigit():
        return None
    parsed_port = int(raw_port, 10)
    if parsed_port < 1 or parsed_port > 65_535:
        return None
    return host, parsed_port


def host_is_non_loopback_listener(host: str) -> bool:
    value = host.strip()
    if value == "*":
        return True
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not address.is_loopback and not address.is_multicast


def has_non_loopback_listener(ss_output: str, expected_port: int) -> bool:
    for line in ss_output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        endpoint = split_listener_endpoint(fields[3])
        if endpoint is None:
            continue
        host, port = endpoint
        if port == expected_port and host_is_non_loopback_listener(host):
            return True
    return False


def read_listeners() -> str:
    try:
        result = subprocess.run(
            [SS_EXECUTABLE, "-H", "-ltn"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DiscoveryPreflightError("unable to inspect TCP listeners") from error
    if len(result.stdout) > 1_048_576:
        raise DiscoveryPreflightError("TCP listener report exceeded safety bound")
    return result.stdout


def main() -> int:
    try:
        port = parse_port(os.environ.get("NOOB_DISCOVERY_SSH_PORT"))
        if not has_non_loopback_listener(read_listeners(), port):
            raise DiscoveryPreflightError(
                f"no non-loopback TCP listener on configured SSH port {port}"
            )
    except DiscoveryPreflightError as error:
        print(f"N.O.O.B. discovery preflight failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
