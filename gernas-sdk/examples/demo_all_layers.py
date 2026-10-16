"""Gernas SDK — 3-layer observability demo (local HTML report edition).

Kept from Beacon Kit and migrated to the Gernas SDK API. Writes to local JSONL
sinks (no Collector needed) and renders the dev HTML report:
    cd gernas-sdk
    python examples/demo_all_layers.py

What this demo produces:
  1. JSONL files in  observability/traces|logs|metrics|compliance/
  2. A self-contained HTML report in  observability/reports/
  3. Exercises EVERY feature of the new report UI:
       - Stats bar (requests, error rate, avg/p95 latency)
       - Gantt span timeline per trace
       - Service call graph (gernas-demo → compliance-agent / data-service / rag-service)
       - Errors tab (one ERROR from Query 3)
       - Structured log attribute chips (agent, user, latency_ms, tokens, …)
       - Cross-tab trace links (Compliance/Logs → Requests)
       - Metrics tab (counters + histograms)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Resolve observability dir ─────────────────────────────────────────────────
# examples/demo_all_layers.py → gernas-sdk/ → repo root → observability/
OBSERVABILITY_DIR = str(Path(__file__).parent.parent.parent / "observability")

from gernas import (
    GernasConfig,
    init_gernas,
    generate_html_report,
    get_logger,
    # Layer 2 decorators
    traced_pipeline,
    trace_agent,
    trace_llm,
    trace_tool,
    trace_delegation,
    trace_mcp,
    track_latency,
    count_calls,
    AgenticMetrics,
    # Layer 3 decorators
    compliance_gate,
    guardrail_check,
    set_default_compliance_config,
    GuardrailBlockedError,
)
from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode

# ═══════════════════════════════════════════════════════════════════════════════
# Bootstrap — Layer 1: Infrastructure (file exporter → observability/ folder)
# ═══════════════════════════════════════════════════════════════════════════════

config = GernasConfig(
    service_name="gernas-demo",
    service_version="0.3.0",
    environment="dev",
    exporter="file",
    file_output_dir=OBSERVABILITY_DIR,
    sampling_rate=1.0,
    use_case_id="UC-DEMO-001",
    business_domain="retail_banking",
    compliance_store=str(Path(OBSERVABILITY_DIR) / "compliance" / "compliance_audit.jsonl"),
    compliance_hmac_key="demo-hmac-key-change-in-prod",
    enable_compliance=True,
)
handle = init_gernas(config)
set_default_compliance_config(config)

log = get_logger("gernas-demo")
metrics = AgenticMetrics()

_tracer = otel_trace.get_tracer("gernas-demo")

print("\n" + "═" * 70)
print("  Gernas SDK  ·  HTML Observability Demo")
print(f"  Writing JSONL → {OBSERVABILITY_DIR}/")
print("═" * 70 + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Simulated cross-service calls (creates spans with different service.name attrs
# so the service call graph shows edges: gernas-demo → child services)
# ═══════════════════════════════════════════════════════════════════════════════

def call_compliance_agent(query: str, user: str) -> dict:
    with _tracer.start_as_current_span(
        "compliance.check",
        attributes={"service.name": "compliance-agent", "query": query, "user": user},
    ) as span:
        time.sleep(0.04)
        result = {"allowed": True, "rule": "content_policy", "action": "allow"}
        span.set_attribute("result", result["action"])
        log.info("Compliance check passed", agent="compliance-agent", user=user,
                 rule="content_policy", action="allow", latency_ms=40)
        return result


def call_data_agent(account_id: str, user: str) -> dict:
    with _tracer.start_as_current_span(
        "data.fetch",
        attributes={"service.name": "data-service", "account_id": account_id},
    ) as span:
        time.sleep(0.06)
        result = {"balance": 9_999.99, "currency": "GBP", "transactions": 12}
        span.set_attribute("records_returned", result["transactions"])
        log.info("Data fetched", agent="data-service", user=user,
                 account_id=account_id, records=result["transactions"], latency_ms=60)
        return result


def call_rag_agent(query: str, user: str, fail: bool = False) -> dict:
    with _tracer.start_as_current_span(
        "rag.search",
        attributes={"service.name": "rag-service", "query": query},
    ) as span:
        time.sleep(0.08)
        if fail:
            span.set_status(otel_trace.Status(StatusCode.ERROR, "Vector DB timeout"))
            span.set_attribute("error.type", "VectorDBTimeout")
            log.error("RAG search failed", agent="rag-service", user=user,
                      error_code="VECTOR_DB_TIMEOUT", latency_ms=80, exc_info=False)
            raise RuntimeError("Vector DB timeout after 80ms")
        result = {"docs": 3, "top_score": 0.92, "latency_ms": 80}
        span.set_attribute("docs_retrieved", result["docs"])
        log.info("RAG search completed", agent="rag-service", user=user,
                 docs=result["docs"], top_score=result["top_score"], latency_ms=80)
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2 decorators (same as before, kept for Spans / Metrics tabs)
# ═══════════════════════════════════════════════════════════════════════════════

@trace_agent("triage-agent", version="1")
@count_calls("demo.agent.calls.total", attributes={"agent_name": "triage-agent"})
def run_triage_agent(query: str, user: str) -> dict:
    time.sleep(0.03)
    intent = (
        "account_query" if "balance" in query.lower()
        else "compliance_query" if "complian" in query.lower()
        else "forecast_query"
    )
    log.info("Triage complete", agent="triage-agent", user=user,
             intent=intent, tokens=120, latency_ms=30)
    return {"intent": intent, "routed_to": "retail_banking"}


@trace_llm("claude-sonnet-4-6", provider="anthropic")
@track_latency("demo.llm.duration", unit="ms")
def call_llm(prompt: str, user: str, inject_pii: bool = False) -> dict:
    time.sleep(0.05)
    content = (
        "Your sort_code is 12-34-56 and IBAN GB29NWBK60161331926819."  # PII — triggers guardrail
        if inject_pii
        else "Based on available data, here is the summary of your account activity."
    )
    log.info("LLM responded", agent="llm", user=user,
             model="claude-sonnet-4-6", tokens=230, latency_ms=50)
    return {"content": content, "usage": {"input_tokens": 180, "output_tokens": 50}}


@trace_tool("account_balance_lookup")
def lookup_balance(account_id: str) -> dict:
    time.sleep(0.02)
    metrics.record_tool_call("success", "account_balance_lookup")
    return {"balance": 9_999.99, "currency": "GBP"}


@compliance_gate("pricing_query_completed", actor_key="actor", run_id_key="run_id")
def compute_pricing(product_id: str, actor: str = "system", run_id: str = "run-001") -> dict:
    time.sleep(0.01)
    return {"product_id": product_id, "price": 29.99}


@guardrail_check(rule_id="pii_filter", action_on_block="warn")
def check_pii(output: str) -> tuple[bool, str]:
    pii_keywords = ["iban", "sort_code", "ssn"]
    for kw in pii_keywords:
        if kw in output.lower():
            return True, f"PII keyword '{kw}' detected in LLM output"
    return False, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Three pipeline scenarios — each becomes one Requests tab row
# ═══════════════════════════════════════════════════════════════════════════════

@traced_pipeline("retail-banking-pipeline", attribute_keys=["query"])
def pipeline_balance_query(query: str, user: str = "alice") -> dict:
    """Query 1: Account balance — SUCCESS, calls compliance + data."""
    log.info("Pipeline started", user=user, pipeline="balance_query", stage="start")

    triage = run_triage_agent(query, user=user)
    metrics.record_agent_call("success", "triage-agent", latency_ms=30)

    compliance = call_compliance_agent(query, user=user)
    data = call_data_agent("ACC-12345", user=user)
    balance = lookup_balance("ACC-12345")

    llm = call_llm(f"Summarise balance for {user}: {data['balance']} GBP", user=user)
    metrics.record_llm_call("success", "claude-sonnet-4-6", latency_ms=50)

    try:
        check_pii(llm["content"], config=config)
    except GuardrailBlockedError:
        pass

    log.info("Pipeline completed", user=user, pipeline="balance_query",
             stage="done", status="success", total_latency_ms=140)
    return {"user": user, "balance": balance, "data": data, "compliance": compliance}


@traced_pipeline("retail-banking-pipeline", attribute_keys=["query"])
def pipeline_compliance_query(query: str, user: str = "bob") -> dict:
    """Query 2: Compliance check — SUCCESS + TRIGGERED guardrail (PII in LLM output)."""
    log.info("Pipeline started", user=user, pipeline="compliance_query", stage="start")

    triage = run_triage_agent(query, user=user)
    metrics.record_agent_call("success", "triage-agent", latency_ms=30)

    compliance = call_compliance_agent(query, user=user)
    data = call_data_agent("ACC-67890", user=user)
    rag = call_rag_agent(query, user=user)

    llm = call_llm(
        f"Compliance check for {user} re: loan pricing",
        user=user,
        inject_pii=True,  # triggers guardrail — surfaces in Compliance tab
    )
    metrics.record_llm_call("success", "claude-sonnet-4-6", latency_ms=50)

    pricing = compute_pricing("LOAN-BB-42", config=config)

    try:
        check_pii(llm["content"], config=config)
    except GuardrailBlockedError as e:
        log.warning("PII guardrail triggered", user=user, rule="pii_filter",
                    action="warn", reason=str(e))

    log.info("Pipeline completed", user=user, pipeline="compliance_query",
             stage="done", status="success_with_warning", total_latency_ms=250)
    return {"user": user, "compliance": compliance, "rag": rag, "pricing": pricing}


@traced_pipeline("retail-banking-pipeline", attribute_keys=["query"])
def pipeline_forecast_query(query: str, user: str = "carol") -> dict:
    """Query 3: Forecast — ERROR in rag-service. Populates Errors tab."""
    log.info("Pipeline started", user=user, pipeline="forecast_query", stage="start")

    triage = run_triage_agent(query, user=user)
    metrics.record_agent_call("success", "triage-agent", latency_ms=30)

    compliance = call_compliance_agent(query, user=user)

    try:
        rag = call_rag_agent(query, user=user, fail=True)  # ERROR span
    except RuntimeError as exc:
        log.warning("RAG fallback activated", user=user, error=str(exc),
                    fallback="static_forecast")
        rag = {"docs": 0, "top_score": 0.0, "fallback": True}

    llm = call_llm(f"Forecast for {user} (no RAG context available)", user=user)
    metrics.record_llm_call("success", "claude-sonnet-4-6", latency_ms=50)

    log.info("Pipeline completed", user=user, pipeline="forecast_query",
             stage="done", status="partial_success", total_latency_ms=200)
    return {"user": user, "compliance": compliance, "rag": rag}


# ═══════════════════════════════════════════════════════════════════════════════
# Run all three scenarios
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    scenarios = [
        ("Query 1 — Account balance (SUCCESS)",
         pipeline_balance_query,
         "What is my account balance and recent transactions?", "alice"),
        ("Query 2 — Compliance check (SUCCESS + PII guardrail triggered)",
         pipeline_compliance_query,
         "Is my loan pricing compliant with BB-rated AED policy?", "bob"),
        ("Query 3 — Spend forecast (ERROR in RAG service)",
         pipeline_forecast_query,
         "Forecast my account spend for next month", "carol"),
    ]

    for label, fn, query, user in scenarios:
        print(f"  [{label}]")
        try:
            result = fn(query=query, user=user)
            print(f"    → done ({len(result)} keys)\n")
        except Exception as exc:
            print(f"    → pipeline raised: {exc}\n")

        time.sleep(0.1)  # brief pause so span timestamps are clearly distinct

    # ── Flush and generate report ─────────────────────────────────────────────
    print("Flushing telemetry …")
    handle.flush(5000)
    handle.shutdown()

    report_path = generate_html_report(config=config)

    print("\n" + "═" * 70)
    print("  ✅  Observability report written:")
    print(f"      file://{report_path}")
    print()
    print("  Open the file in your browser to explore:")
    print("    • Stats bar     — 3 requests, ~33% error rate, latency percentiles")
    print("    • Requests tab  — expand any row to see the Gantt span timeline")
    print("    • Service graph — gernas-demo → compliance / data / rag agents")
    print("    • Logs tab      — click attribute chips to filter by field value")
    print("    • Errors tab    — rag.search failure from Query 3")
    print("    • Compliance    — PII guardrail TRIGGERED from Query 2")
    print("    • Metrics tab   — LLM and agent call counters + latency histogram")
    print("═" * 70 + "\n")
