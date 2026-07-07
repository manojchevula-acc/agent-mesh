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


class Step(str, Enum):
    """The pipeline steps that consume an LLM. Each can use a different model."""
    AGENT = "agent"            # ReAct tool selection (agent/graph.py)
    GENERATION = "generation"  # tier-3 dynamic SQL generation (query_engine.py)
    CORRECTION = "correction"  # self-correction retry after a validator error
    JUDGE = "judge"            # optional LLM-as-judge / evaluation hook
    INTENT = "intent"          # intent classification (small/fast is fine)
    PLAN = "plan"              # schema-link planner — precision task, wants a stronger model
    DEFAULT = "default"


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    temperature: float


def _resolve(step: Step) -> ResolvedModel:
    """Apply the per-step override on top of the default, per step."""
    overrides = {
        Step.AGENT: (settings.llm_agent_provider, settings.llm_agent_model),
        Step.GENERATION: (settings.llm_generation_provider, settings.llm_generation_model),
        Step.CORRECTION: (settings.llm_correction_provider, settings.llm_correction_model),
        Step.JUDGE: (settings.llm_judge_provider, settings.llm_judge_model),
        Step.INTENT: (settings.llm_intent_provider, settings.llm_intent_model),
        Step.PLAN: (settings.llm_plan_provider, settings.llm_plan_model),
        Step.DEFAULT: ("", ""),
    }
    ov_provider, ov_model = overrides.get(step, ("", ""))
    provider = (ov_provider or settings.llm_provider).strip().lower()
    model = (ov_model or settings.llm_model).strip()
    return ResolvedModel(provider=provider, model=model, temperature=settings.llm_temperature)


# --- Provider builders --------------------------------------------------------

def _build_groq(rm: ResolvedModel):
    from langchain_groq import ChatGroq
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    return ChatGroq(model=rm.model, api_key=settings.groq_api_key,
                    temperature=rm.temperature)


def _build_openai(rm: ResolvedModel):
    from langchain_openai import ChatOpenAI
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return ChatOpenAI(model=rm.model, api_key=settings.openai_api_key,
                      temperature=rm.temperature)


def _build_azure(rm: ResolvedModel):
    from langchain_openai import AzureChatOpenAI
    if not settings.azure_openai_api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not configured.")
    # For Azure the deployment name is the routing key; rm.model overrides it if set.
    deployment = rm.model or settings.azure_openai_deployment
    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        azure_deployment=deployment,
        api_version=settings.azure_openai_api_version,
        temperature=rm.temperature,
    )


def _build_anthropic(rm: ResolvedModel):
    from langchain_anthropic import ChatAnthropic
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return ChatAnthropic(model=rm.model, api_key=settings.anthropic_api_key,
                         temperature=rm.temperature)


_PROVIDER_BUILDERS = {
    "groq": _build_groq,
    "openai": _build_openai,
    "azure": _build_azure,
    "anthropic": _build_anthropic,
}


@lru_cache(maxsize=None)
def _get_cached(provider: str, model: str, temperature: float):
    rm = ResolvedModel(provider=provider, model=model, temperature=temperature)
    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Supported: {', '.join(sorted(_PROVIDER_BUILDERS))}"
        )
    return builder(rm)


def get_llm(step: Step | str = Step.DEFAULT):
    """Return a LangChain chat model for the given pipeline step.

    Models are cached per (provider, model, temperature) so repeated calls for the
    same step reuse one client.
    """
    if isinstance(step, str):
        step = Step(step)
    rm = _resolve(step)
    return _get_cached(rm.provider, rm.model, rm.temperature)
