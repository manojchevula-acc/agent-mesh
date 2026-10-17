"""Security and redaction (Blueprint §2 critical principle): no secrets, raw identity or payloads."""

from __future__ import annotations

import pytest

import gernas
from gernas import Ctx, SpanName
from gernas.redaction import redact
from gernas.tracer import MAX_ATTRIBUTE_LENGTH, safe_set
from gernas.utils.sanitizer import scrub_pii, scrub_secrets

from conftest import attrs


@pytest.mark.parametrize(
    "key", ["gen_ai.prompt", "gen_ai.completion", "gen_ai.input.messages", "enduser.id", Ctx.USER_ID]
)
def test_denylisted_keys_are_dropped(spans, key):
    @gernas.trace_tool("t")
    def tool():
        gernas.annotate({key: "raw value"})

    tool()
    a = attrs(spans(SpanName.TOOL)[0])
    assert key not in a and a[Ctx.REDACTION_APPLIED] is True


@pytest.mark.parametrize(
    "raw,needle",
    [
        ("Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGci"),
        ("key sk-abcdefghijklmnopqrstuvwx", "sk-abcdef"),
        ("password=hunter2", "hunter2"),
        ("Emirates ID 784-1990-1234567-1", "784-1990"),
        ("IBAN AE070331234567890123456", "AE0703"),
        ("card 4111 1111 1111 1111", "4111 1111"),
        ("mail jane@example.com", "jane@"),
    ],
)
def test_unregistered_free_text_is_scrubbed(raw, needle):
    value, changed = redact("custom.note", raw)
    assert changed and needle not in value


def test_registered_identifiers_are_kept_but_credentials_are_removed():
    run_id = "run_0123456789abcdef0123456789abcdef"
    assert redact(Ctx.AGENT_RUN_ID, run_id) == (run_id, False)
    value, changed = redact(Ctx.API_OPERATION, "GET /x?api_key=supersecretvalue")
    assert changed and "supersecretvalue" not in value
    assert scrub_secrets("plain id 12345678901") == "plain id 12345678901"
    assert scrub_pii("call +971 50 123 4567") != "call +971 50 123 4567"


def test_redaction_hook_runs_first_and_can_drop(spans):
    def hook(key, value):
        if key == Ctx.TOOL_ID and value == "internal-secret-tool":
            return gernas.DROP
        return value

    gernas.set_redaction_hook(hook)
    try:

        @gernas.trace_tool("internal-secret-tool")
        def tool():
            return 1

        tool()
    finally:
        gernas.set_redaction_hook(None)
    a = attrs(spans(SpanName.TOOL)[0])
    assert Ctx.TOOL_ID not in a and a[Ctx.REDACTION_APPLIED] is True


def test_attribute_length_is_capped(spans):
    @gernas.trace_tool("t")
    def tool():
        gernas.annotate({Ctx.API_OPERATION: "x" * 10_000})

    tool()
    assert len(attrs(spans(SpanName.TOOL)[0])[Ctx.API_OPERATION]) == MAX_ATTRIBUTE_LENGTH


def test_prompts_completions_and_tool_arguments_are_never_captured(spans):
    secret_prompt = "My account 12345678901234 balance?"

    @gernas.trace_llm("gpt-4o", provider="azure_openai")
    def chat(messages):
        return {
            "choices": [{"message": {"content": "Your balance is 900"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    @gernas.trace_tool("lookup")
    def lookup(account):
        return {"balance": 900}

    chat([{"role": "user", "content": secret_prompt}])
    lookup("12345678901234")
    for span in spans():
        values = " ".join(str(v) for v in attrs(span).values())
        assert "12345678901234" not in values and "balance" not in values.lower()


def test_safe_set_never_raises_and_counts_drops(spans):
    from gernas import tracer

    class Broken:
        end_time = None

        def set_attribute(self, k, v):
            raise RuntimeError("boom")

    before = tracer.dropped_attributes.value
    safe_set(Broken(), Ctx.TOOL_ID, "x")
    assert tracer.dropped_attributes.value == before + 1


def test_telemetry_failure_never_breaks_the_function(monkeypatch):
    from gernas.decorators import _core

    def explode(*a, **k):
        raise RuntimeError("telemetry bug")

    monkeypatch.setattr(_core, "stamp_context", explode)

    @gernas.trace_tool("t")
    def tool():
        return 42

    assert tool() == 42


def test_otlp_api_key_is_sent_as_header_not_attribute():
    from gernas.config import GernasConfig
    from gernas.exporters import _headers

    cfg = GernasConfig(
        service_name="s",
        use_case_id="u",
        application_id="a",
        business_domain="b",
        environment="dev",
        exporter="off",
        otlp_api_key="k-123",
    )
    assert _headers(cfg) == {"authorization": "Bearer k-123"}
    assert "k-123" not in str(dict(cfg.resource.attributes))
