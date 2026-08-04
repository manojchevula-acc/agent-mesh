"""Evaluation configuration."""

from pydantic import BaseModel


class EvaluationConfig(BaseModel):
    # Which backend powers the RAGAS LLM judge.
    #   'groq'   → hosted Groq API (needs llm.groq_api_key)
    #   'ollama' → local model served by Ollama's OpenAI-compatible endpoint
    #   'openai' → OpenAI (or any OpenAI-compatible endpoint via judge_base_url)
    judge_provider: str = "groq"

    # Base URL for the judge when provider is 'ollama' or 'openai'.
    # For Ollama this is the OpenAI-compatible endpoint, e.g. http://localhost:11434/v1.
    # Ignored for the 'groq' provider.
    judge_base_url: str | None = None

    # API key for the judge endpoint. Ollama ignores the value but the OpenAI SDK
    # requires a non-empty string, so a placeholder like "ollama" is fine.
    judge_api_key: str | None = None

    # Model used by RAGAS as the LLM judge (separate from the answer-generation LLM).
    # Use a small/fast model to stay within free-tier TPM limits.
    # For Ollama use the local tag, e.g. "llama3.1:8b" or "mistral".
    judge_model: str = "qwen/qwen3.6-27b"

    # Max output tokens for the judge. Faithfulness emits a per-claim verdict list
    # (one JSON object per statement in the answer), which easily exceeds 1k tokens
    # for multi-clause policy answers — too small a cap truncates the response and
    # RAGAS raises LLMDidNotFinishException, silently dropping the faithfulness score.
    # NOTE: on the Groq on-demand tier, input + output share ONE 8000
    # tokens-per-minute budget, PER REQUEST — not just across a minute of separate
    # requests. Raising this eats directly into how much context (max_context_chars)
    # a single call can carry before that one request alone exceeds 8000 and Groq
    # returns a 413, no amount of retrying will get a too-large request through.
    judge_max_tokens: int = 2048

    # Max characters per context chunk sent to the RAGAS judge. Too small drops the
    # sentence the answer/ground-truth depends on (e.g. a pricing-table row deep in a
    # ~5.4k-char parent chunk), collapsing context_recall and faithfulness to ~0. Too
    # large and (context * top_k) + judge_max_tokens alone can exceed a single
    # request's entire token budget on a constrained tier (see judge_max_tokens).
    # Override per env with RAG__EVALUATION__MAX_CONTEXT_CHARS.
    max_context_chars: int = 3000

    # HuggingFace model used by RAGAS answer_relevancy metric for semantic similarity.
    # Defaults to a tiny 22 MB model — avoids any OpenAI dependency.
    # Override with BAAI/bge-m3 to reuse the same model as retrieval.
    embeddings_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # The four fields below all become a ragas.run_config.RunConfig, passed into
    # ragas.evaluate(). RAGAS's own defaults (max_workers=16, timeout=180,
    # max_retries=10, max_wait=60) assume a generously-provisioned API. Against a
    # free-tier Groq account sharing one small tokens-per-minute budget, 16
    # concurrent jobs queue behind each other's rate-limit backoff and blow past a
    # 180s timeout before any of them finish — a mass TimeoutError cascade, not a
    # sign anything is broken. Low concurrency + a long, patient timeout is what
    # makes a heavily-throttled account actually work instead of just fail slower.

    # RAGAS's own concurrency. 1 keeps jobs strictly sequential so each one gets
    # the full per-minute token budget to itself; raise it only on a provider/tier
    # that can sustain real concurrency.
    judge_max_workers: int = 1

    # How many times RAGAS retries a single job's LLM call on failure (its own
    # retry loop, on top of whatever the LLM client itself does).
    judge_max_retries: int = 15

    # Max backoff between RAGAS's own retries, in seconds.
    judge_max_wait: int = 90

    # RAGAS's own per-job timeout, in seconds. Needs to be generous when
    # max_workers is low and max_retries/max_wait are high — a job may need
    # several patient retries across multiple minutes before the shared
    # tokens-per-minute budget frees up enough to let it through.
    judge_timeout_seconds: int = 900
