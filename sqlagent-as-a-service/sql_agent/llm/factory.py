"""LLM factory — resolves a chat model per pipeline step from config.

Resolution order for a step:
  1. Per-step override (e.g. LLM_GENERATION_PROVIDER / LLM_GENERATION_MODEL).
  2. The default (LLM_PROVIDER / LLM_MODEL).

Providers supported: groq, openai, azure, anthropic. Add a new provider by adding one
builder to ``_PROVIDER_BUILDERS`` — nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from sql_agent.config import settings
from sql_agent.logging_config import get_logger

log = get_logger("llm")


class Step(str, Enum):
    """The pipeline steps that consume an LLM. Each can use a different model."""
    AGENT = "agent"            # ReAct tool selection (agent/graph.py)
    GENERATION = "generation"  # tier-3 dynamic SQL generation (query_engine.py)
    CORRECTION = "correction"  # self-correction retry after a validator error
    JUDGE = "judge"            # optional LLM-as-judge / evaluation hook
    INTENT = "intent"          # intent classification (small/fast is fine)
    PLAN = "plan"              # schema-link planner — precision task, wants a stronger model
    SYNTHESIS = "synthesis"    # dedicated final-answer writer (agent/graph.py), gated by
                               # settings.response_synthesis_enabled
    DEFAULT = "default"


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    temperature: float
    api_key: str = ""


# Per-step Groq API keys (blank => fall back to the default groq_api_key). Only applied
# when the step's resolved provider is groq. Spreads load across keys to raise the
# effective per-minute rate limit.
_GROQ_STEP_KEYS = {
    Step.AGENT: "groq_api_key_agent",
    Step.GENERATION: "groq_api_key_generation",
    Step.CORRECTION: "groq_api_key_correction",
    Step.JUDGE: "groq_api_key_judge",
    Step.INTENT: "groq_api_key_intent",
    Step.PLAN: "groq_api_key_plan",
    Step.SYNTHESIS: "groq_api_key_synthesis",
}


def _resolve_groq_key(step: Step) -> str:
    """Per-step Groq key, falling back to the shared default when the step has none."""
    per_step = getattr(settings, _GROQ_STEP_KEYS.get(step, ""), "") if step in _GROQ_STEP_KEYS else ""
    return (per_step or settings.groq_api_key).strip()


def _resolve(step: Step) -> ResolvedModel:
    """Apply the per-step override on top of the default, per step."""
    overrides = {
        Step.AGENT: (settings.llm_agent_provider, settings.llm_agent_model,
                    settings.llm_agent_temperature),
        Step.GENERATION: (settings.llm_generation_provider, settings.llm_generation_model, None),
        Step.CORRECTION: (settings.llm_correction_provider, settings.llm_correction_model, None),
        Step.JUDGE: (settings.llm_judge_provider, settings.llm_judge_model, None),
        Step.INTENT: (settings.llm_intent_provider, settings.llm_intent_model, None),
        Step.PLAN: (settings.llm_plan_provider, settings.llm_plan_model, None),
        Step.SYNTHESIS: (settings.llm_synthesis_provider, settings.llm_synthesis_model,
                         settings.llm_synthesis_temperature),
        Step.DEFAULT: ("", "", None),
    }
    ov_provider, ov_model, ov_temperature = overrides.get(step, ("", "", None))
    provider = (ov_provider or settings.llm_provider).strip().lower()
    model = (ov_model or settings.llm_model).strip()
    temperature = settings.llm_temperature if ov_temperature is None else ov_temperature
    # Only groq consumes a per-step key here; other providers use their own credential.
    api_key = _resolve_groq_key(step) if provider == "groq" else ""
    return ResolvedModel(provider=provider, model=model, temperature=temperature,
                         api_key=api_key)


# --- Provider builders --------------------------------------------------------
# Each returns (chat_client, default_options): MAF puts sampling parameters on the
# CALL (ChatOptions), not on the client, so callers merge default_options into their
# per-call ChatOptions (see llm/step.py).

# Groq model-name substrings that mean "this is a reasoning model" — it will emit a
# hidden <think> trace before its real answer unless reasoning_format="parsed" is set,
# and can burn its whole output budget on that trace unless max_tokens gives it room
# for both. Mapped to the reasoning_effort VALUE each family actually accepts — Groq
# rejects the wrong enum outright (confirmed empirically: gpt-oss takes low/medium/
# high/default, qwen3 takes only none/default — "low" against qwen3 is a 400
# "reasoning_effort must be one of none or default"). "none" for qwen3 is also simply
# the right choice here: JUDGE/SYNTHESIS are answer-formatting steps, not reasoning
# tasks, so suppressing the trace entirely (not just hiding it) is what avoids both
# the leak and the truncation at the source, not just its symptoms.
_REASONING_EFFORT_BY_MARKER = {
    "gpt-oss": None,   # None = "use settings.groq_reasoning_effort" (low/medium/high)
    "qwen": "none",
}

def _build_groq(rm: ResolvedModel):
    """Groq via its OpenAI-compatible endpoint. MAF ships no Groq-specific client;
    Groq serves /openai/v1 in the CHAT COMPLETIONS shape, not the newer Responses API.

    RawOpenAIChatCompletionClient, deliberately NOT RawOpenAIChatClient and NOT
    OpenAIChatClient:
      * RawOpenAIChatClient / OpenAIChatClient call client.responses.create(...) (the
        OpenAI Responses API, /v1/responses). Groq does not implement that endpoint —
        only /v1/chat/completions — so those classes would 404 against Groq.
      * RawOpenAIChatCompletionClient calls client.chat.completions.create(...), the
        endpoint Groq actually serves, and is also what langchain-groq/ChatGroq called.
      * "Raw" (not the layered OpenAIChatCompletionClient) because that layered class's
        MRO adds FunctionInvocationLayer, which auto-executes tools; this codebase runs
        its own tool step. Verified in docs/maf/reference_spike.py.

    reasoning_effort / reasoning_format: RawOpenAIChatCompletionClient._prepare_options
    copies every key from ChatOptions straight into the **kwargs of
    chat.completions.create(). reasoning_effort is a real, typed parameter on the
    openai SDK's create() (added for o-series reasoning models) so it passes through
    directly. reasoning_format is a Groq-only extension the SDK does NOT recognise as a
    named parameter — passing it flat raises "unexpected keyword argument" from the SDK
    itself (confirmed empirically). It has to travel inside extra_body, which the SDK
    merges verbatim into the raw JSON request body; extra_body is itself a real SDK
    parameter, so it passes through _prepare_options the same flat way.

    max_tokens: reasoning_format="parsed" only relocates the hidden <think> trace out
    of .content — it does NOT exempt it from the token budget. A verbose reasoner can
    still burn the entire (unset-Groq-default) budget on reasoning and leave nothing
    for the visible answer, which surfaced live as a well-formed sentence cut off
    mid-word. groq_reasoning_max_output_tokens gives it enough headroom for both.
    """
    from agent_framework.openai import RawOpenAIChatCompletionClient
    api_key = rm.api_key or settings.groq_api_key
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    extra: dict = {}
    model_lower = rm.model.lower()
    matched = next((m for m in _REASONING_EFFORT_BY_MARKER if m in model_lower), None)
    if matched is not None:
        # Reasoning models on Groq can under-invest in the visible final answer if
        # left on Groq's default reasoning effort/budget — reasoning_format="parsed"
        # keeps any reasoning out of .content, the (family-specific) effort value
        # biases token budget toward actually answering rather than reasoning at
        # length, and max_tokens guarantees room for the answer even when it doesn't.
        effort = _REASONING_EFFORT_BY_MARKER[matched]
        extra["reasoning_effort"] = effort if effort is not None else settings.groq_reasoning_effort
        extra["extra_body"] = {"reasoning_format": "parsed"}
        extra["max_tokens"] = settings.groq_reasoning_max_output_tokens
    return RawOpenAIChatCompletionClient(
        rm.model,
        api_key=api_key,
        base_url=settings.groq_base_url,           # default https://api.groq.com/openai/v1
    ), {"temperature": rm.temperature, **extra}


def _build_openai(rm: ResolvedModel):
    # Chat Completions client (not the Responses-API RawOpenAIChatClient) to match the
    # wire behaviour of the old langchain-openai ChatOpenAI, which also used
    # /v1/chat/completions.
    from agent_framework.openai import RawOpenAIChatCompletionClient
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return RawOpenAIChatCompletionClient(rm.model, api_key=settings.openai_api_key), \
        {"temperature": rm.temperature}


def _build_azure(rm: ResolvedModel):
    # There is NO AzureOpenAIChatClient in agent_framework.azure (that module holds the
    # Durable clients). The same RawOpenAIChatCompletionClient serves Azure via
    # azure_endpoint, matching AzureChatOpenAI's Chat Completions wire behaviour.
    from agent_framework.openai import RawOpenAIChatCompletionClient
    if not settings.azure_openai_api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not configured.")
    # For Azure the deployment name is the routing key; rm.model overrides it if set.
    deployment = rm.model or settings.azure_openai_deployment
    return RawOpenAIChatCompletionClient(
        deployment,
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
    ), {"temperature": rm.temperature}


def _build_anthropic(rm: ResolvedModel):
    # Confirmed present in 1.16.0 (alongside AnthropicBedrockClient / AnthropicVertexClient).
    # Requires the agent-framework-anthropic distribution.
    from agent_framework.anthropic import AnthropicClient
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return AnthropicClient(rm.model, api_key=settings.anthropic_api_key), \
        {"temperature": rm.temperature}


_PROVIDER_BUILDERS = {
    "groq": _build_groq,
    "openai": _build_openai,
    "azure": _build_azure,
    "anthropic": _build_anthropic,
}


@lru_cache(maxsize=None)
def _get_cached(provider: str, model: str, temperature: float, api_key: str = ""):
    rm = ResolvedModel(provider=provider, model=model, temperature=temperature,
                       api_key=api_key)
    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Supported: {', '.join(sorted(_PROVIDER_BUILDERS))}"
        )
    return builder(rm)          # -> (client, default_options)


def get_llm(step: Step | str = Step.DEFAULT):
    """Return (chat_client, default_options) for the given pipeline step.

    Clients are cached per (provider, model, temperature, api_key) so repeated calls
    for the same step reuse one client, and different per-step Groq keys get their
    own client. Unchanged from the LangGraph version apart from the tuple.
    """
    if isinstance(step, str):
        step = Step(step)
    rm = _resolve(step)
    return _get_cached(rm.provider, rm.model, rm.temperature, rm.api_key)


def log_usage(step: Step | str, response) -> None:
    """Log token usage for one LLM call, tagged by pipeline step and model.

    MAF normalises usage onto ChatResponse.usage_details. UsageDetails, like
    ChatOptions and ToolMode, is a TypedDict — a plain dict at runtime, not an object —
    so this reads it with .get(), not getattr(). Confirmed empirically: an earlier
    getattr()-based version silently logged None for every field despite the response
    actually carrying token counts.
    """
    step_name = step.value if isinstance(step, Step) else str(step)
    rm = _resolve(step if isinstance(step, Step) else Step(step))
    usage = getattr(response, "usage_details", None)
    if not usage:
        log.debug("TOKENS step=%s model=%s | usage metadata unavailable",
                   step_name, rm.model)
        return
    input_tokens = usage.get("input_token_count")
    output_tokens = usage.get("output_token_count")
    total_tokens = usage.get("total_token_count")
    log.info("TOKENS step=%s model=%s | input=%s output=%s total=%s",
              step_name, rm.model, input_tokens, output_tokens, total_tokens)
