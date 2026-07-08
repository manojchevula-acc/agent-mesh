"""LLM package — configurable, multi-provider, per-step model selection.

Every step of the pipeline asks for its model by name (``get_llm(Step.GENERATION)``);
the factory resolves provider + model from config, applying per-step overrides over the
default. Swapping providers (Groq / OpenAI / Azure / Anthropic) or models per step is a
config change, not a code change.
"""

from .factory import Step, get_llm, log_usage

__all__ = ["Step", "get_llm", "log_usage"]
