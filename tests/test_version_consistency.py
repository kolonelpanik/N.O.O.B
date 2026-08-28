from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.2.0"


def _python_constant(path: Path, name: str) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError(f"{name} is not a top-level string constant in {path}")


def test_product_version_surfaces_are_aligned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    operator = json.loads((ROOT / "operator" / "package.json").read_text(encoding="utf-8"))
    operator_lock = json.loads(
        (ROOT / "operator" / "package-lock.json").read_text(encoding="utf-8")
    )
    plugin = json.loads(
        (ROOT / "integrations" / "noob-plugin" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    plugin_manifest = json.loads(
        (
            ROOT
            / "integrations"
            / "noob-plugin"
            / ".claude-plugin"
            / "plugin.json"
        ).read_text(encoding="utf-8")
    )

    gateway_lock = next(
        package for package in uv_lock["package"] if package["name"] == "noob-gateway"
    )
    versions = {
        "python project": project["project"]["version"],
        "python lock": gateway_lock["version"],
        "gateway package": _python_constant(
            ROOT / "gateway" / "noob_gateway" / "__init__.py", "__version__"
        ),
        "Pico firmware": _python_constant(ROOT / "pico" / "code.py", "FIRMWARE_VERSION"),
        "operator package": operator["version"],
        "operator lock": operator_lock["version"],
        "operator lock root package": operator_lock["packages"][""]["version"],
        "agent plugin": plugin["version"],
        "agent plugin manifest": plugin_manifest["version"],
    }
    assert versions == {name: EXPECTED_VERSION for name in versions}, versions

    camera_cmake = (ROOT / "camera" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert f'set(PROJECT_VER "{EXPECTED_VERSION}")' in camera_cmake


def test_discovery_advertisement_and_documentation_publish_current_version():
    unit = (ROOT / "packaging" / "noob-discovery.service").read_text(encoding="utf-8")
    documentation = (ROOT / "docs" / "appliance-discovery.md").read_text(
        encoding="utf-8"
    )

    assert re.search(rf"\bversion={re.escape(EXPECTED_VERSION)}\b", unit)
    assert f"version={EXPECTED_VERSION}" in documentation
