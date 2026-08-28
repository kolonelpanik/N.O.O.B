#!/usr/bin/env python3
"""Run one bounded ESP-IDF build action with release values redacted."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


FIELDS = (
    "CONFIG_NOOB_CAMERA_PROVISIONING_POP",
    "CONFIG_NOOB_CAMERA_PROVISIONING_AP_KEY",
    "CONFIG_NOOB_CAMERA_API_TOKEN",
)
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_-]+$")
ACTIONS = {"build", "size", "size-components"}
REDACTION = "[REDACTED_CAMERA_RELEASE_VALUE]"


class RedactionError(RuntimeError):
    """Raised when a protected build cannot be filtered safely."""


def read_release_values(path: Path) -> tuple[str, ...]:
    if not path.is_file() or path.is_symlink():
        raise RedactionError("sdkconfig must be a regular non-symlink file")
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        for field in FIELDS:
            prefix = f'{field}="'
            if line.startswith(prefix) and line.endswith('"'):
                if field in found:
                    raise RedactionError("sdkconfig contains a duplicate release field")
                value = line[len(prefix) : -1]
                if not SAFE_VALUE.fullmatch(value):
                    raise RedactionError("sdkconfig release field is absent or unsafe")
                found[field] = value
    if set(found) != set(FIELDS) or len(set(found.values())) != len(FIELDS):
        raise RedactionError("sdkconfig release fields are incomplete or reused")
    return tuple(found[field] for field in FIELDS)


def redact_line(line: str, values: tuple[str, ...]) -> str:
    for value in values:
        line = line.replace(value, REDACTION)
    return line


def run_action(sdkconfig: Path, action: str) -> int:
    if action not in ACTIONS:
        raise RedactionError("unsupported ESP-IDF action")
    executable = shutil.which("idf.py")
    if executable is None:
        raise RedactionError("idf.py is unavailable")
    values = read_release_values(sdkconfig)
    environment = os.environ.copy()
    environment["KCONFIG_REPORT_VERBOSITY"] = "quiet"
    process = subprocess.Popen(
        [executable, action],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=environment,
    )
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(redact_line(line, values))
        sys.stdout.flush()
    return process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdkconfig", type=Path, required=True)
    parser.add_argument("action", choices=sorted(ACTIONS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_action(args.sdkconfig, args.action)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RedactionError as error:
        raise SystemExit(f"ERROR: {error}") from error
