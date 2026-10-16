"""Engineering principles 1 & hard prohibitions, enforced in CI."""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_IMPORTS = ("openai", "anthropic", "azure.ai", "boto3")


def test_no_auto_instrumentation_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = list(project["dependencies"]) + [d for extra in project["optional-dependencies"].values() for d in extra]
    assert not [d for d in deps if d.startswith("opentelemetry-instrumentation")]


def test_core_modules_do_not_import_vendor_sdks():
    offenders = []
    for path in (ROOT / "gernas").rglob("*.py"):
        if "adapters" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORTS) or name.startswith("opentelemetry.instrumentation"):
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders


def test_version_has_a_single_source():
    import gernas
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in project["project"]["dynamic"]
    assert gernas.__version__ == "0.3.0"


def test_no_beacon_kit_names_remain_in_the_package():
    hits = []
    for path in (ROOT / "gernas").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("beacon_kit", "BEACON_", "beacon."):
            if token in text:
                hits.append(f"{path.relative_to(ROOT)}: {token}")
    assert not hits
