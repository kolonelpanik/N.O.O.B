#!/usr/bin/env python3
"""Display the public SSH identity in short and advanced N.O.O.B. forms."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

DEFAULT_PUBLIC_KEY = Path("/etc/ssh/ssh_host_ed25519_key.pub")
DOMAIN_SEPARATOR = b"N.O.O.B. pairing code v1\0"


def fingerprint_for_public_key(text: str) -> str:
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("expected an OpenSSH Ed25519 public host key")
    try:
        key = base64.b64decode(fields[1], validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("invalid OpenSSH public host key") from error
    if not 32 <= len(key) <= 16_384:
        raise ValueError("invalid OpenSSH public host key")
    encoded = base64.b64encode(hashlib.sha256(key).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def pairing_code_for_fingerprint(fingerprint: str) -> str:
    digest = hashlib.sha256(DOMAIN_SEPARATOR + fingerprint.encode("ascii")).digest()
    value = int.from_bytes(digest[:4], "big") % 100_000_000
    digits = f"{value:08d}"
    return f"{digits[:4]}-{digits[4:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Show this uConsole's N.O.O.B. SSH pairing identity")
    parser.add_argument("public_key", nargs="?", type=Path, default=DEFAULT_PUBLIC_KEY)
    args = parser.parse_args()
    text = args.public_key.read_text(encoding="ascii")
    if len(text) > 16_384:
        raise SystemExit("public key file is unexpectedly large")
    fingerprint = fingerprint_for_public_key(text)
    print(f"N.O.O.B. pairing code: {pairing_code_for_fingerprint(fingerprint)}")
    print(f"Advanced SSH fingerprint: {fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
