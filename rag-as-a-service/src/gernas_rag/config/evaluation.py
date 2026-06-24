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
    # NOTE: on the Groq free tier, input + output share a 6k tokens-per-minute budget,
    # so if you raise this you may need to lower *_max_context_chars to avoid 413s.
    judge_max_tokens: int = 4096

    # Max characters per context chunk sent to the RAGAS judge in the batch run.
    # Too small drops the sentence the answer/ground-truth depends on (e.g. a
    # pricing-table row deep in a ~5.4k-char parent chunk), collapsing
    # context_recall and faithfulness to ~0. Override per env with
    # RAG__EVALUATION__MAX_CONTEXT_CHARS; lower it if you raise top_k and hit
    # Groq TPM 413/429s.
    max_context_chars: int = 6000

    # Max characters per context for single-answer (reference-free) scoring.
    # Only one row is judged, so we can afford much more context — small caps here
    # truncate away the parent text the answer was grounded in and tank faithfulness.
    # Tuned for grounding vs. judge latency: at ~4000 the free-tier judge took ~4 min;
    # 3000 keeps it comfortably under the UI timeout while still covering most parents.
    # Override per env with RAG__EVALUATION__SINGLE_ANSWER_MAX_CONTEXT_CHARS.
    single_answer_max_context_chars: int = 6000

    # top_k used during evaluation retrieval (fewer = smaller judge prompts).
    top_k: int = 3

    # HuggingFace model used by RAGAS answer_relevancy metric for semantic similarity.
    # Defaults to a tiny 22 MB model — avoids any OpenAI dependency.
    # Override with BAAI/bge-m3 to reuse the same model as retrieval.
    embeddings_model: str = "sentence-transformers/all-MiniLM-L6-v2"
