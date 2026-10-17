"""Registry validity, generated artefacts up to date, Collector blocks in sync."""

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


def test_thirteen_blueprint_domains_plus_documented_extensions():
    gen = _generator()
    reg = gen.load()
    fields = gen.validate(reg)
    blueprint = [d for d in reg["domains"] if d.get("blueprint")]
    assert len(blueprint) == 13 and len(reg["domains"]) == 15
    for f in fields.values():
        for prop in gen.REQUIRED_PROPS:
            assert prop in f, (f["name"], prop)
        assert str(f["source"]).startswith(("GERNAS-REQ-", "TN-")), f["name"]


def _field(reg, name):
    for dom in reg["domains"]:
        for f in dom["fields"]:
            if f["name"] == name:
                return dom, f
    raise KeyError(name)


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda r: r["domains"][0]["fields"].append(dict(r["domains"][0]["fields"][0])), "duplicate"),
        (lambda r: _field(r, "AGENT_ID")[1].update(sensitivity="topsecret"), "sensitivity"),
        (lambda r: _field(r, "AGENT_RUN_ID")[1].update(rollup=True), "rollup dimension"),
        (lambda r: _field(r, "USER_ID")[1].update(action="keep"), "action=drop"),
        (lambda r: r["spans"]["spans"][0]["required"].append("NOPE"), "unknown required field"),
        (lambda r: r["domains"][0]["fields"].pop(), "Blueprint"),
        (lambda r: _field(r, "USE_CASE_ID")[1].update(key="gernas.usecase"), "Blueprint"),
        (lambda r: r["domains"].pop(0), "missing from the registry"),
    ],
)
def test_validator_rejects_bad_registries(mutation, message):
    gen = _generator()
    reg = copy.deepcopy(gen.load())
    mutation(reg)
    with pytest.raises(gen.RegistryError, match=message):
        gen.validate(reg)


def test_redaction_matrix_covers_every_field_with_an_action():
    from gernas.attributes import REGISTRY

    matrix = yaml.safe_load((ROOT / "registry" / "redaction-matrix.yaml").read_text(encoding="utf-8"))
    from gernas.attributes import DENYLIST

    rows = {r["field"]: r for r in matrix["fields"]}
    native = {"trace_id (span context)", "span_id (span context)"}
    assert set(rows) == set(REGISTRY) | set(DENYLIST) | native
    assert all(r["action"] and r["collector_rule"] for r in rows.values())
    assert all(rows[k]["action"] == "drop" for k in DENYLIST)


def test_unbounded_fields_are_never_rollup_dimensions():
    gen = _generator()
    fields = gen.validate(gen.load())
    assert not [f["key"] for f in fields.values() if f["cardinality"] == "unbounded" and f["rollup"]]


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
    keep = {k for k, s in REGISTRY.items() if s.action != "drop" and k not in ("gernas.trace_id", "gernas.span_id")}
    dropped = {k for k, s in REGISTRY.items() if s.action == "drop"}
    assert keep <= allowed and not (dropped & allowed)


def test_collector_denylist_matches_the_registry():
    from gernas.attributes import DENYLIST

    text = (ROOT / "collector" / "otel-collector-config.yaml").read_text(encoding="utf-8")
    block = text.split("GERNAS-DENYLIST (generated", 1)[1].split("<<< GERNAS-DENYLIST", 1)[0]
    deleted = {line.split("key:", 1)[1].split(",", 1)[0].strip() for line in block.splitlines() if "key:" in line}
    assert deleted == set(DENYLIST)
    assert "route_opik" not in text and "tokenised" not in text


def test_semantic_conventions_file_is_valid_yaml_for_other_languages():
    doc = yaml.safe_load((ROOT / "registry" / "semantic-conventions.yaml").read_text(encoding="utf-8"))
    groups = {g["id"] for g in doc["groups"]}
    assert "registry.gernas.business" in groups and any(g.startswith("span.gernas.") for g in groups)
