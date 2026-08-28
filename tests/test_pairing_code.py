from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "noob_pairing_code.py"
SPEC = importlib.util.spec_from_file_location("noob_pairing_code", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_pairing_code_matches_the_cross_client_domain_separated_algorithm() -> None:
    key = bytes([47]) * 32
    public = f"ssh-ed25519 {base64.b64encode(key).decode('ascii')} appliance\n"
    fingerprint = MODULE.fingerprint_for_public_key(public)
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(key).digest()).decode("ascii").rstrip("=")
    assert fingerprint == expected
    code = MODULE.pairing_code_for_fingerprint(fingerprint)
    assert len(code) == 9
    assert code[4] == "-"
    assert code.replace("-", "").isdigit()


def test_pairing_code_rejects_non_ed25519_material() -> None:
    try:
        MODULE.fingerprint_for_public_key("ssh-rsa AAAA")
    except ValueError as error:
        assert "Ed25519" in str(error)
    else:
        raise AssertionError("non-Ed25519 host key was accepted")
