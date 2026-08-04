"""RAGAS metrics (faithfulness, answer_relevancy, context_precision, context_recall)
over the same recorded ``GenerationRun`` and ``gold_qa.json`` that power the rest of
stage 4.

``gold_qa.json``'s ``expected_answer`` is RAGAS's reference answer — there is no
second, hand-maintained ground-truth set for this. Judge and embeddings backends are
configured separately from both the answer-generation LLM and the deterministic
``AnswerJudge`` (see ``settings.evaluation``), since a judge tuned for cheap/fast
generation is not necessarily a good grader.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math

from gernas_rag.config.settings import Settings
from gernas_rag.embeddings.base import BaseEmbedder

from ..core.models import GenerationRun
from ..core.text import strip_citations

_METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def _patch_ragas_vertexai_import() -> None:
    """RAGAS 0.4.x imports ChatVertexAI from langchain_community, removed upstream in
    langchain_community >= 0.2. Stub it out before importing anything from ragas.
    """
    import sys
    import types

    mod_name = "langchain_community.chat_models.vertexai"
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)

        class ChatVertexAI:  # noqa: N801
            pass

        stub.ChatVertexAI = ChatVertexAI  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub


class _EmbeddingsBridge:
    """Sync LangChain ``Embeddings`` wrapper around our async ``BaseEmbedder``.

    RAGAS calls ``embed_documents``/``embed_query`` synchronously; the coroutine runs
    in a worker thread with its own event loop so it doesn't conflict with whatever
    async loop is already running (e.g. the eval CLI's own).
    """

    def __init__(self, embedder: BaseEmbedder) -> None:
        self._embedder = embedder

    def _run(self, coro):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        output = self._run(self._embedder.embed_documents(texts))
        return output.dense_vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def make_ragas_embeddings(settings: Settings, embedder: BaseEmbedder | None):
    """RAGAS-compatible embeddings. Prefers the already-loaded embedder (no second
    model load), falling back to a HuggingFace model configured via
    ``settings.evaluation.embeddings_model``.
    """
    from ragas.embeddings import LangchainEmbeddingsWrapper

    if embedder is not None:
        return LangchainEmbeddingsWrapper(_EmbeddingsBridge(embedder))

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError:
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore[no-redef]

    return LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=settings.evaluation.embeddings_model))


def make_ragas_llm(settings: Settings):
    """RAGAS ``LangchainLLMWrapper`` for the judge backend selected by
    ``settings.evaluation.judge_provider``: ``groq`` (hosted), ``ollama`` (local, via
    its OpenAI-compatible endpoint) or ``openai`` (OpenAI or any OpenAI-compatible
    server).
    """
    from ragas.llms import LangchainLLMWrapper

    eval_cfg = settings.evaluation
    provider = eval_cfg.judge_provider.lower()

    if provider == "ollama":
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=eval_cfg.judge_model,
            base_url=eval_cfg.judge_base_url or "http://localhost:11434/v1",
            api_key=eval_cfg.judge_api_key or "ollama",
            temperature=0,
            max_tokens=eval_cfg.judge_max_tokens,
        )
        # bypass_n: answer_relevancy asks for `strictness` completions (default 3)
        # in one call. Most OpenAI-compatible providers — Groq confirmed, Ollama
        # likely — reject n>1 and RAGAS would otherwise silently fall back to a
        # single generation per call, making relevancy noisier than intended.
        # bypass_n makes the wrapper issue `strictness` separate n=1 calls instead.
        return LangchainLLMWrapper(chat, bypass_n=True)

    if provider == "groq" and settings.llm.groq_api_key:
        from langchain_groq import ChatGroq

        chat = ChatGroq(
            model=eval_cfg.judge_model,
            api_key=settings.llm.groq_api_key,
            temperature=0,
            max_tokens=eval_cfg.judge_max_tokens,
        )
        # bypass_n: answer_relevancy asks for `strictness` completions (default 3)
        # in one call. Most OpenAI-compatible providers — Groq confirmed, Ollama
        # likely — reject n>1 and RAGAS would otherwise silently fall back to a
        # single generation per call, making relevancy noisier than intended.
        # bypass_n makes the wrapper issue `strictness` separate n=1 calls instead.
        return LangchainLLMWrapper(chat, bypass_n=True)

    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(
        model=eval_cfg.judge_model,
        base_url=eval_cfg.judge_base_url,
        api_key=eval_cfg.judge_api_key,
        temperature=0,
        max_tokens=eval_cfg.judge_max_tokens,
    )
    return LangchainLLMWrapper(chat)


def _build_rows(run: GenerationRun, gold: list[dict], max_context_chars: int) -> tuple[list[str], list[dict]]:
    """One row per answerable, answered question in ``gold`` with a recorded answer.

    ``gold`` is caller-scoped — pass the full gold set to score everything, or a
    filtered subset (e.g. the run's ``--id``/``--limit`` selection) to score only
    those questions. Either way, only entries present in ``gold`` are scored.

    Returns ``(question_ids, rows)`` with matching order/length, so a row's score can
    be re-attached to its gold id after ``ragas.evaluate`` returns.
    """
    gold_by_id = {str(item["id"]): item for item in gold if item.get("answerable", True)}
    by_id = run.by_id()

    ids: list[str] = []
    rows: list[dict] = []
    for question_id, item in gold_by_id.items():
        record = by_id.get(question_id)
        if record is None or not record.answer.strip():
            continue
        ids.append(question_id)
        rows.append(
            {
                "question": item["question"],
                "answer": strip_citations(record.answer),
                "contexts": [c.text[:max_context_chars] for c in record.contexts] or [""],
                "ground_truth": item.get("expected_answer", ""),
            }
        )
    return ids, rows


def run_ragas(
    run: GenerationRun,
    gold: list[dict],
    settings: Settings,
    embedder: BaseEmbedder | None = None,
) -> dict[str, dict[str, float]]:
    """Score every answerable, answered question in ``gold`` with RAGAS.

    ``gold`` is caller-scoped (see ``_build_rows``) — the caller is responsible for
    merging the returned scores into any previously-cached ones if it passed a
    subset, since this function only ever returns scores for what it was asked to
    score, never the full history.

    Returns ``{question_id: {metric_name: score}}``. Rows RAGAS could not score
    (NaN — e.g. a judge parse failure) are simply absent from that question's dict,
    the same "missing, not a false 0" convention ``eval.core.metrics.mean`` expects.
    """
    _patch_ragas_vertexai_import()

    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    ids, rows = _build_rows(run, gold, settings.evaluation.max_context_chars)
    if not rows:
        return {}

    result = evaluate(
        Dataset.from_list(rows),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=make_ragas_llm(settings),
        embeddings=make_ragas_embeddings(settings, embedder),
        # RAGAS's own default is max_workers=16 / timeout=180s — far more
        # concurrency than a free-tier Groq TPM budget can sustain, which is what
        # produces a mass TimeoutError cascade rather than a clean queue. See
        # EvaluationConfig.judge_max_workers/judge_timeout.
        run_config=RunConfig(
            max_workers=settings.evaluation.judge_max_workers,
            timeout=settings.evaluation.judge_timeout_seconds,
            max_retries=settings.evaluation.judge_max_retries,
            max_wait=settings.evaluation.judge_max_wait,
        ),
    )

    scores: dict[str, dict[str, float]] = {}
    for question_id, row_scores in zip(ids, result.scores):
        entry = {
            name: float(row_scores[name])
            for name in _METRIC_NAMES
            if name in row_scores
            and row_scores[name] is not None
            and not math.isnan(float(row_scores[name]))
        }
        if entry:
            scores[question_id] = entry
    return scores
