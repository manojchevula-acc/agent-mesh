"""Layer 3 audit chain kept from Beacon Kit: chained, signed, verifiable events."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentelemetry import baggage, context

import gernas
from gernas import Ctx
from gernas.compliance.chain import verify_chain


def _events(cfg) -> list[dict]:
    path = Path(cfg.compliance_store)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_compliance_gate_chains_and_signs(cfg):
    @gernas.compliance_gate("pricing_query_completed")
    def answer(actor="rm", run_id="r1"):
        return {"price": 1}

    @gernas.compliance_gate("compliance_violation_detected")
    def fail(actor="rm", run_id="r2"):
        raise RuntimeError("x")

    answer()
    with pytest.raises(RuntimeError):
        fail()
    events = [e for e in _events(cfg) if "chain_hash" in e]
    assert {e["outcome"] for e in events[-2:]} == {"PASS", "FAIL"}
    assert all(len(e["signature"]) == 64 for e in events)
    assert verify_chain(events)
    tampered = [dict(events[0], outcome="FAIL" if events[0]["outcome"] == "PASS" else "PASS"), *events[1:]]
    assert not verify_chain(tampered)


def test_guardrail_check_blocks_and_records(cfg):
    @gernas.guardrail_check("no_injection", action_on_block="block")
    def screen(text):
        return (True, "injection")

    with pytest.raises(gernas.GuardrailBlockedError):
        screen("x")
    assert any(e.get("rule_id") == "no_injection" and e["triggered"] for e in _events(cfg))


def test_rbac_check_reads_role_from_baggage(cfg):
    @gernas.rbac_check("credit_officer", resource="limits")
    def adjust():
        return "ok"

    token = context.attach(baggage.set_baggage(Ctx.USER_ROLE, "credit_officer"))
    try:
        assert adjust() == "ok"
    finally:
        context.detach(token)
    with pytest.raises(gernas.RBACDeniedError):
        adjust()
    outcomes = [e["outcome"] for e in _events(cfg) if e.get("resource") == "limits"]
    assert outcomes[-2:] == ["ALLOW", "DENY"]
