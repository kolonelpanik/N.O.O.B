from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "camera" / "scripts" / "launch_uconsole_provision.sh"
SHELL_TEST = ROOT / "camera" / "scripts" / "test_launch_uconsole_provision.sh"


def test_launcher_shell_syntax() -> None:
    subprocess.run(
        ["/bin/bash", "-n", str(LAUNCHER), str(SHELL_TEST)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_launcher_shell_contract() -> None:
    completed = subprocess.run(
        ["/bin/bash", str(SHELL_TEST)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == "PASS: uConsole provisioning launcher shell contract\n"
    assert completed.stderr == ""


def test_launcher_uses_fixed_systemd_credential_source_and_internal_entry() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'readonly MATERIAL_SOURCE="/run/noob-camera-device-material.json"' in source
    assert '"--property=LoadCredential=material:${MATERIAL_SOURCE}"' in source
    assert "ExecStartPre=" not in source
    assert '--property=StandardOutput=null' in source
    assert '--property=StandardError=null' in source
    assert 'material="${credentials_directory}/material"' in source
    assert '--material-file "$material"' in source
    assert '"${SYSTEMD_RUN_COMMAND[@]}" >/dev/null 2>&1' in source


def test_service_entry_removes_source_only_after_validating_credential_copy() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    start = source.index("service_entry()")
    end = source.index("launch()", start)
    entry = source[start:end]
    first_copy_validation = entry.index('validate_material_file "$material"')
    cleanup = entry.index(
        'remove_material_source_after_credential_copy "$MATERIAL_SOURCE"'
    )
    second_copy_validation = entry.index(
        'validate_material_file "$material"', first_copy_validation + 1
    )
    runtime_validation = entry.index("validate_runtime_values")
    provisioner_exec = entry.index('exec "$PYTHON_RUNTIME" "$PROVISIONER"')
    assert (
        first_copy_validation
        < cleanup
        < second_copy_validation
        < runtime_validation
        < provisioner_exec
    )


def test_python_runtime_has_a_dedicated_bounded_symlink_validator() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'validate_root_python_runtime "$PYTHON_RUNTIME"' in source
    assert 'candidate="$(/usr/bin/dirname -- "$current")/${target}"' in source
    assert '(( depth <= 16 ))' in source
    assert '"$label symlink target did not resolve absolutely"' in source
    assert '"$label symlink chain is broken"' in source
