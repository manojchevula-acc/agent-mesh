"""Naming contract: the exact span, metric, attribute, env-var and public API names.

Blueprint §2 field names are checked against the blueprint list held in
``registry/generate.py`` (copied verbatim from the blueprint). Every other name is
pinned here so a rename is a deliberate, reviewed contract change.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import gernas
from gernas.attributes import DENYLIST, ENVELOPE, REGISTRY, REQUIRED_ATTRIBUTES, RUN_CONTEXT, Ctx, Metric, SpanName

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location("gernas_registry_generate_naming", ROOT / "registry" / "generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_blueprint_context_field_has_exactly_the_blueprint_name():
    blueprint = _generator().BLUEPRINT_CONTEXT
    assert len(blueprint) == 13
    keys = set(REGISTRY)
    for domain, fields in blueprint.items():
        for name in fields:
            if name in ("trace_id", "span_id"):
                continue  # native OTel span context, not attributes
            assert f"gernas.{name}" in keys, (domain, name)


def test_attribute_keys_are_flat_gernas_snake_case_without_aliases():
    otel_http = {Ctx.HTTP_METHOD, Ctx.HTTP_ROUTE, Ctx.HTTP_STATUS_CODE}  # OTel HTTP semconv (TN-03)
    assert otel_http == {"http.request.method", "http.route", "http.response.status_code"}
    gernas_keys = [k for k in REGISTRY if k not in otel_http]
    for key in gernas_keys:
        assert re.fullmatch(r"gernas\.[a-z][a-z0-9_]*", key), key
    suffixes = [k.split(".", 1)[1] for k in gernas_keys]
    assert len(suffixes) == len(set(suffixes))
    # one constant per key: no two Ctx names point at the same key
    values = [v for k, v in vars(Ctx).items() if k.isupper()]
    assert len(values) == len(set(values))


def test_no_otel_genai_or_legacy_keys_are_emitted_by_the_sdk():
    legacy = ("gen_ai.", "tokenised", "est_cost", "beacon", "gernas.route", "gernas.trace_id", "gernas.span_id")
    for key in REGISTRY:
        assert not any(key.startswith(p) or p in key for p in legacy), key
    assert {"gen_ai.prompt", "gen_ai.completion", "gernas.user_id"} <= DENYLIST


def test_span_names_are_exact():
    assert {k: v for k, v in vars(SpanName).items() if k.isupper()} == {
        "USE_CASE": "use_case.request",
        "AGENT": "agent.run",
        "STEP": "agent.step",
        "PLANNING": "agent.plan",
        "DELEGATION": "agent.delegate",
        "LLM": "llm.call",
        "EMBEDDING": "embedding.create",
        "TOOL": "tool.call",
        "RETRIEVAL": "retrieval.search",
        "MEMORY": "memory.access",
        "GUARDRAIL": "guardrail.check",
        "CONTENT_SAFETY": "content_safety.check",
        "DOCUMENT": "document.process",
        "EVALUATION": "evaluation.score",
        "HITL": "hitl.approval",
        "HTTP": "{method} {route}",
    }
    assert set(REQUIRED_ATTRIBUTES) == {v for k, v in vars(SpanName).items() if k.isupper()}


def test_metric_names_are_exact():
    assert sorted(v for k, v in vars(Metric).items() if k.isupper()) == sorted(
        [
            "gernas.use_case.requests",
            "gernas.agent.runs",
            "gernas.agent.steps",
            "gernas.agent.plan.duration",
            "gernas.fallbacks",
            "gernas.llm.ttft",
            "gernas.llm.tokens_per_second",
            "gernas.evaluation.score",
            "gernas.documents",
            "gernas.document.chunks",
            "gernas.agent.run.duration",
            "gernas.agent.retries",
            "gernas.agent.loops",
            "gernas.agent.delegations",
            "gernas.agent.delegation.duration",
            "gernas.llm.calls",
            "gernas.llm.duration",
            "gernas.llm.tokens",
            "gernas.llm.estimated_cost",
            "gernas.tool.calls",
            "gernas.tool.duration",
            "gernas.retrieval.calls",
            "gernas.policy.decisions",
            "gernas.hitl.interventions",
            "gernas.sdk.dropped_spans",
            "gernas.sdk.dropped_logs",
            "gernas.sdk.dropped_attributes",
            "gernas.sdk.redactions",
        ]
    )


def test_envelope_and_run_context():
    assert ENVELOPE == (
        Ctx.BUSINESS_DOMAIN,
        Ctx.USE_CASE_ID,
        Ctx.APPLICATION_ID,
        Ctx.ENVIRONMENT,
        Ctx.OPERATION,
        Ctx.EXECUTION_STATUS,
    )
    assert RUN_CONTEXT == (Ctx.AGENT_ID, Ctx.AGENT_RUN_ID)


def test_environment_variables_are_exact():
    source = (ROOT / "gernas" / "config.py").read_text(encoding="utf-8")
    assert set(re.findall(r'"(GERNAS_[A-Z_]+)"', source)) == {
        "GERNAS_SERVICE_NAME",
        "GERNAS_USE_CASE_ID",
        "GERNAS_APPLICATION_ID",
        "GERNAS_BUSINESS_DOMAIN",
        "GERNAS_ENVIRONMENT",
        "GERNAS_SERVICE_VERSION",
        "GERNAS_AGENT_VERSION",
        "GERNAS_CLUSTER",
        "GERNAS_NAMESPACE",
        "GERNAS_POD",
        "GERNAS_REGION",
        "GERNAS_EXPORTER",
        "GERNAS_OTLP_ENDPOINT",
        "GERNAS_OTLP_TRANSPORT",
        "GERNAS_OTLP_API_KEY",
        "GERNAS_OTLP_TIMEOUT_S",
        "GERNAS_SAMPLING_RATE",
        "GERNAS_METRIC_INTERVAL_MS",
        "GERNAS_LOG_NAMESPACES",
        "GERNAS_LOOP_THRESHOLD",
        "GERNAS_MAX_STEPS",
        "GERNAS_RATE_CARD_PATH",
        "GERNAS_USER_TOKEN_KEY",
        "GERNAS_SUPPRESSED_SCOPES",
    }


def test_public_api_is_exact():
    assert sorted(gernas.__all__) == sorted(
        [
            "__version__",
            "init_gernas",
            "GernasConfig",
            "GernasConfigError",
            "GernasHandle",
            "Ctx",
            "SpanName",
            "Metric",
            "trace_use_case",
            "trace_agent",
            "trace_step",
            "trace_planning",
            "trace_delegation",
            "trace_llm",
            "trace_embedding",
            "trace_tool",
            "trace_retrieval",
            "trace_document",
            "trace_memory",
            "trace_guardrail",
            "trace_content_safety",
            "trace_hitl",
            "start_hitl",
            "complete_hitl",
            "record_retry",
            "set_termination_reason",
            "mark_fallback",
            "record_evaluation",
            "annotate",
            "request_context",
            "correlation",
            "inject_context",
            "extract_context",
            "attach_headers",
            "gernas_task",
            "wrap_in_context",
            "set_redaction_hook",
            "DROP",
        ]
    )
    for name in gernas.__all__:
        assert hasattr(gernas, name), name


def test_console_entry_point():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"] == {"gernas": "gernas.cli:main"}
    assert project["name"] == "gernas-telemetry"
