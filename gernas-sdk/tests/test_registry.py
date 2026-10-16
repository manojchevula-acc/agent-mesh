"""WP1-CTX-01/02/05/06: registry validity and generated artefacts are up to date."""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location("gernas_registry_generate", ROOT / "registry" / "generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_artefacts_are_up_to_date():
    """CI gate: editing attributes.py by hand (or the YAML without regenerating) fails."""
    assert _generator().main(["--check"]) == 0


def test_sixteen_domains_with_complete_entries():
    gen = _generator()
    reg = gen.load()
    fields = gen.validate(reg)
    assert len(reg["domains"]) == 16
    for f in fields.values():
        for prop in gen.REQUIRED_PROPS:
            assert prop in f, (f["name"], prop)


@pytest.mark.parametrize("mutation,message", [
    (lambda r: r["domains"][0]["fields"].append(dict(r["domains"][0]["fields"][0])), "duplicate"),
    (lambda r: r["domains"][0]["fields"][0].update(sensitivity="topsecret"), "sensitivity"),
    (lambda r: r["domains"][3]["fields"][0].update(rollup=True), "rollup dimension"),
    (lambda r: r["domains"][1]["fields"][0].update(action="keep"), "PDPL"),
    (lambda r: r["spans"]["spans"][0]["required"].append("NOPE"), "unknown required field"),
    (lambda r: r["domains"].pop(), "expected 16 domains"),
])
def test_validator_rejects_bad_registries(mutation, message):
    gen = _generator()
    reg = copy.deepcopy(gen.load())
    mutation(reg)
    with pytest.raises(gen.RegistryError, match=message):
        gen.validate(reg)


def test_redaction_matrix_covers_every_field_with_an_action():
    from gernas.attributes import REGISTRY
    matrix = yaml.safe_load((ROOT / "registry" / "redaction-matrix.yaml").read_text(encoding="utf-8"))
    rows = {r["field"]: r for r in matrix["fields"]}
    assert set(rows) == set(REGISTRY)
    assert all(r["action"] and r["collector_rule"] for r in rows.values())


def test_unbounded_fields_are_never_rollup_dimensions():
    from gernas.attributes import REGISTRY
    assert not [k for k, s in REGISTRY.items() if s.cardinality == "unbounded" and s.rollup]


def test_collector_allowlist_matches_registry():
    from gernas.attributes import REGISTRY
    text = (ROOT / "collector" / "otel-collector-config.yaml").read_text(encoding="utf-8")
    allowed = set()
    inside = False
    for line in text.splitlines():
        if "GERNAS-ALLOWLIST (generated" in line:
            inside = True
            continue
        if "<<< GERNAS-ALLOWLIST" in line:
            break
        if inside and line.strip().startswith("- "):
            allowed.add(line.strip()[2:])
    keep = {k for k, s in REGISTRY.items() if s.action != "drop"}
    dropped = {k for k, s in REGISTRY.items() if s.action == "drop"}
    assert keep <= allowed and not (dropped & allowed)


def test_semantic_conventions_file_is_valid_yaml_for_other_languages():
    doc = yaml.safe_load((ROOT / "registry" / "semantic-conventions.yaml").read_text(encoding="utf-8"))
    groups = {g["id"] for g in doc["groups"]}
    assert "registry.gernas.business" in groups and "span.gernas.agent" in groups
