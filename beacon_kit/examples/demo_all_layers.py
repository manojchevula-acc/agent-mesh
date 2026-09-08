"""beacon_kit — 3-layer observability demo.

Run from the repo root:
    cd beacon_kit
    python examples/demo_all_layers.py

What you will see:
  - Rich waterfall span tree (Layer 1 + Layer 2)
  - Metrics table (Layer 2 counters/histograms)
  - Compliance JSONL written to ./compliance_audit.jsonl (Layer 3)
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# ── Make beacon_kit importable when running from the beacon_kit/ folder ───────
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from beacon_kit import (
    TelemetryConfig,
    setup_telemetry,
    flush_telemetry,
    # Layer 2 decorators
    traced_pipeline,
    traced_agent,
    traced_llm_call,
    traced_tool,
    traced_a2a_hop,
    traced_mcp_tool,
    track_latency,
    count_calls,
    AgenticMetrics,
    # Layer 3 decorators
    compliance_gate,
    guardrail_check,
    set_default_compliance_config,
    GuardrailBlockedError,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Bootstrap — Layer 1: Infrastructure
# ═══════════════════════════════════════════════════════════════════════════════

config = TelemetryConfig(
    service_name="beacon-demo",
    service_version="0.2.0",
    environment="dev",
    exporter_type="pretty",          # Rich waterfall in the terminal
    sampling_rate=1.0,
    enable_baggage_propagation=True,
    compliance_store="./compliance_audit.jsonl",
    compliance_hmac_key="demo-hmac-key-change-in-prod",
    enable_compliance=True,
)

handle = setup_telemetry(config)
set_default_compliance_config(config)  # so Layer 3 decorators don't need config= kwarg

metrics = AgenticMetrics()

print("\n" + "═" * 70)
print("  beacon_kit  •  3-layer observability demo")
print("═" * 70 + "\n")

# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2 — Agentic Interactions
# ═══════════════════════════════════════════════════════════════════════════════

# ── @traced_agent ─────────────────────────────────────────────────────────────
@traced_agent("triage-agent", attributes={"agent.version": "1"})
@count_calls("beacon.agent.calls.total", attributes={"agent_name": "triage-agent"})
def run_triage_agent(query: str) -> dict:
    time.sleep(0.05)  # simulate work
    return {
        "response": "routed to retail_banking",
        "intent": "account_query",
        "token_usage": {"input_tokens": 120, "output_tokens": 45},
    }


# ── @traced_llm_call ──────────────────────────────────────────────────────────
@traced_llm_call(model="claude-sonnet-4-6")
@track_latency("beacon.llm.duration", unit="ms")
def call_llm(prompt: str) -> dict:
    time.sleep(0.08)
    return {
        "content": "The answer is 42.",
        "model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 200, "output_tokens": 30},
    }


# ── @traced_tool ──────────────────────────────────────────────────────────────
@traced_tool("account_balance_lookup")
def lookup_balance(account_id: str) -> dict:
    time.sleep(0.02)
    return {"balance": 9_999.99, "currency": "GBP"}


# ── @traced_mcp_tool ──────────────────────────────────────────────────────────
@traced_mcp_tool(service="data-service", tool_name="fetch_transactions")
def fetch_transactions(account_id: str, limit: int = 10) -> list:
    time.sleep(0.03)
    return [{"id": f"txn_{i}", "amount": i * 10.0} for i in range(limit)]


# ── @traced_a2a_hop ───────────────────────────────────────────────────────────
@traced_a2a_hop(target_node="fraud-detection-agent")
def call_fraud_agent(payload: dict, carrier: dict | None = None) -> dict:
    # carrier is auto-populated with W3C traceparent by the decorator
    time.sleep(0.04)
    print(f"  [A2A] Outbound W3C headers injected into carrier: {carrier}")
    return {"fraud_score": 0.03, "decision": "allow"}


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 3 — Compliance
# ═══════════════════════════════════════════════════════════════════════════════

@compliance_gate("pricing_query_completed", actor_key="actor", run_id_key="run_id")
def compute_pricing(product_id: str, actor: str = "system", run_id: str = "run-001") -> dict:
    time.sleep(0.01)
    return {"product_id": product_id, "price": 29.99}


@guardrail_check(rule_id="pii_filter", action_on_block="warn")
def check_pii_in_output(output: str) -> tuple[bool, str]:
    pii_keywords = ["iban", "sort_code", "ssn"]
    for kw in pii_keywords:
        if kw in output.lower():
            return True, f"PII keyword '{kw}' detected"
    return False, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Root pipeline — wraps everything in a single trace
# ═══════════════════════════════════════════════════════════════════════════════

@traced_pipeline("retail-banking-pipeline", attribute_keys=["query"])
def retail_banking_pipeline(query: str) -> dict:
    print(f"\n[PIPELINE] Processing query: '{query}'\n")

    # Layer 2: agent invocation
    triage = run_triage_agent(query)
    metrics.record_agent_call("success", "triage-agent", latency_ms=50)

    # Layer 2: LLM call
    llm_result = call_llm(prompt=f"Summarize intent for: {query}")
    metrics.record_llm_call("success", "claude-sonnet-4-6", latency_ms=80)

    # Layer 2: tool calls
    balance = lookup_balance(account_id="ACC-12345")
    metrics.record_tool_call("success", "account_balance_lookup")

    transactions = fetch_transactions(account_id="ACC-12345", limit=3)
    metrics.record_mcp_call("success", "data-service", "fetch_transactions")

    # Layer 2: A2A hop — carrier dict is auto-injected by @traced_a2a_hop
    outbound_carrier: dict = {}
    fraud = call_fraud_agent(payload={"query": query}, carrier=outbound_carrier)
    metrics.record_a2a_call("success", "fraud-detection-agent", latency_ms=40)

    # Layer 3: compliance event
    pricing = compute_pricing("PROD-42", config=config)

    # Layer 3: guardrail check (warn-only, won't raise)
    llm_text = llm_result.get("content", "")
    try:
        check_pii_in_output(llm_text, config=config)
    except GuardrailBlockedError as e:
        print(f"  [GUARDRAIL] Blocked: {e}")

    return {
        "triage": triage,
        "balance": balance,
        "transactions": transactions,
        "fraud": fraud,
        "pricing": pricing,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Run the demo
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    result = retail_banking_pipeline(query="What is my account balance and any recent fraud?")

    print("\n[PIPELINE RESULT]")
    for k, v in result.items():
        print(f"  {k}: {v}")

    print("\n[FLUSHING TELEMETRY]")
    flush_telemetry(handle, timeout_millis=3000)
    handle.shutdown()

    compliance_path = Path("./compliance_audit.jsonl")
    if compliance_path.exists():
        lines = compliance_path.read_text().strip().splitlines()
        print(f"\n[COMPLIANCE AUDIT] {len(lines)} event(s) written to {compliance_path.resolve()}")
        for line in lines:
            print(f"  {line}")
    else:
        print("\n[COMPLIANCE AUDIT] No events written (compliance_store path check).")

    print("\n" + "═" * 70)
    print("  Demo complete — check the span waterfall above.")
    print("═" * 70 + "\n")
