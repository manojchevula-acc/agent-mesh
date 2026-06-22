"""RAGEvaluator — runs RAGAS metrics over the FAB test set."""

import asyncio
import concurrent.futures
import math
import re

from langchain_core.embeddings import Embeddings

from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..generation.generator import ResponseGenerator
from ..models.retrieval import RetrieveRequest
from ..retrieval.pipeline import RetrievalPipeline
from ..utils.logging import get_logger
from .metrics import METRIC_THRESHOLDS, REFERENCE_FREE_THRESHOLDS
from .test_dataset import TEST_CASES

logger = get_logger(__name__)

# Inline source citations the generator appends to answers, e.g. "【1 · 4.85】"
# or "[1 · p2_c1]". RAGAS faithfulness decomposes the answer into atomic claims
# and tries to ground each in the context; these markers become unverifiable
# "claims" ("4.85", "p2_c1") that tank the score. Strip them before judging.
_CITATION_RE = re.compile(r"【[^】]*】|\[[^\]]*·[^\]]*\]")


def _strip_citations(text: str) -> str:
    """Remove inline citation markers and tidy the leftover whitespace."""
    cleaned = _CITATION_RE.sub("", text)
    # Collapse spaces left before punctuation / doubled spaces from removed markers.
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


class _EmbeddingsBridge(Embeddings):
    """Sync LangChain Embeddings wrapper around our async BaseEmbedder.

    RAGAS calls embed_documents/embed_query synchronously. We run the
    coroutine in a worker thread with its own event loop so it doesn't
    conflict with the main async event loop already running.
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


class RAGEvaluator:
    def __init__(
        self,
        pipeline: RetrievalPipeline,
        generator: ResponseGenerator,
        settings: Settings | None = None,
        embedder: BaseEmbedder | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._generator = generator
        self._settings = settings
        self._embedder = embedder

    def _make_ragas_embeddings(self):
        """Return a RAGAS-compatible embeddings object.

        Prefers the already-loaded embedder (no second model load).
        Falls back to a HuggingFace model configured via settings.evaluation.embeddings_model.
        """
        from ragas.embeddings import LangchainEmbeddingsWrapper

        if self._embedder:
            logger.info("RAGAS embeddings: reusing loaded embedder")
            return LangchainEmbeddingsWrapper(_EmbeddingsBridge(self._embedder))

        eval_cfg = self._settings.evaluation if self._settings else None
        model = eval_cfg.embeddings_model if eval_cfg else "sentence-transformers/all-MiniLM-L6-v2"

        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError:
            from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore[no-redef]

        logger.info("RAGAS embeddings model (fallback)", model=model)
        return LangchainEmbeddingsWrapper(HuggingFaceEmbeddings(model_name=model))

    def _make_ragas_llm(self):
        """Build a RAGAS LangchainLLMWrapper using config from settings.evaluation.

        The judge backend is selected by ``settings.evaluation.judge_provider``:
        ``groq`` (hosted), ``ollama`` (local, via its OpenAI-compatible endpoint)
        or ``openai`` (OpenAI or any OpenAI-compatible server).
        """
        from ragas.llms import LangchainLLMWrapper

        eval_cfg = self._settings.evaluation if self._settings else None
        judge_model = eval_cfg.judge_model if eval_cfg else "llama-3.1-8b-instant"
        judge_max_tokens = eval_cfg.judge_max_tokens if eval_cfg else 4096
        provider = (eval_cfg.judge_provider if eval_cfg else "groq").lower()

        if provider == "ollama":
            from langchain_openai import ChatOpenAI

            base_url = (eval_cfg.judge_base_url if eval_cfg else None) or "http://localhost:11434/v1"
            api_key = (eval_cfg.judge_api_key if eval_cfg else None) or "ollama"
            chat = ChatOpenAI(
                model=judge_model,
                base_url=base_url,
                api_key=api_key,
                temperature=0,
                max_tokens=judge_max_tokens,
            )
            logger.info("RAGAS judge LLM", model=judge_model, provider="ollama", base_url=base_url)
            return LangchainLLMWrapper(chat)

        if provider == "groq" and self._settings and self._settings.llm.groq_api_key:
            from langchain_groq import ChatGroq
            chat = ChatGroq(
                model=judge_model,
                api_key=self._settings.llm.groq_api_key,
                temperature=0,
                max_tokens=judge_max_tokens,
            )
            logger.info("RAGAS judge LLM", model=judge_model, provider="groq")
            return LangchainLLMWrapper(chat)

        from langchain_openai import ChatOpenAI

        base_url = eval_cfg.judge_base_url if eval_cfg else None
        api_key = eval_cfg.judge_api_key if eval_cfg else None
        chat = ChatOpenAI(
            model=judge_model,
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            max_tokens=judge_max_tokens,
        )
        logger.info("RAGAS judge LLM", model=judge_model, provider="openai", base_url=base_url)
        return LangchainLLMWrapper(chat)

    @staticmethod
    def _patch_ragas_vertexai_import() -> None:
        # RAGAS 0.4.x imports ChatVertexAI from langchain_community which was
        # removed in langchain_community >= 0.2. Stub it to prevent ImportError.
        import sys
        import types
        mod_name = "langchain_community.chat_models.vertexai"
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            class ChatVertexAI:  # noqa: N801
                pass
            stub.ChatVertexAI = ChatVertexAI  # type: ignore[attr-defined]
            sys.modules[mod_name] = stub

    def _score_rows(self, rows: list[dict], metrics: list, thresholds: dict) -> dict:
        """Run RAGAS ``metrics`` over ``rows`` and return per-metric score + pass/fail.

        RAGAS 0.4.x returns an ``EvaluationResult`` whose ``.scores`` is a list of
        per-row ``{metric_name: value}`` dicts. We average each metric across rows
        (ignoring NaNs) rather than relying on ``dict(result)``, which is not a
        mapping in this version.
        """
        from datasets import Dataset
        from ragas import evaluate

        result = evaluate(
            Dataset.from_list(rows),
            metrics=metrics,
            llm=self._make_ragas_llm(),
            embeddings=self._make_ragas_embeddings(),
        )

        per_row: list[dict] = list(result.scores)
        metric_results: dict[str, dict] = {}
        for name, threshold in thresholds.items():
            values = [
                float(r[name])
                for r in per_row
                if name in r and r[name] is not None and not math.isnan(float(r[name]))
            ]
            if not values:
                continue
            score = sum(values) / len(values)
            metric_results[name] = {"score": round(score, 4), "pass": score >= threshold}
        return metric_results

    async def evaluate_answer(
        self,
        question: str,
        answer: str,
        contexts: list[str],
    ) -> dict:
        """Reference-free score for a single, already-generated answer.

        Scores one Q+A+contexts triple — e.g. a live user query from the Search
        page — with faithfulness, answer_relevancy and context_utilization. No
        retrieval or generation happens here; the caller supplies the answer and
        the contexts it was grounded in, so there is no ground truth to compare
        against.
        """
        self._patch_ragas_vertexai_import()

        from ragas.metrics import answer_relevancy, faithfulness
        from ragas.metrics._context_precision import context_utilization

        # Single row → judge against the full context the model actually used, so
        # faithfulness/context_utilization reflect real grounding. No truncation
        # here: only one row is scored, so we don't risk the batch TPM limits.
        row = {
            "question": question,
            "answer": _strip_citations(answer),
            "contexts": list(contexts),
        }
        metric_results = self._score_rows(
            [row],
            [faithfulness, answer_relevancy, context_utilization],
            REFERENCE_FREE_THRESHOLDS,
        )
        logger.info("Single-answer evaluation complete", results=metric_results)
        return {
            "metrics": metric_results,
            "reference_free": True,
            "all_pass": all(v["pass"] for v in metric_results.values()),
        }

    async def run(
        self,
        reference_free: bool = False,
        test_cases: list[dict] | None = None,
        limit: int | None = None,
        top_k: int | None = None,
    ) -> dict:
        """Run RAGAS evaluation. Returns metric scores and pass/fail per metric.

        Args:
            reference_free: When True, evaluate without ground truth using only
                faithfulness, answer_relevancy and context_utilization. Lets the
                evaluation run over questions that have no gold answer (e.g. live
                production queries). When False (default), runs the full set
                including context_precision and context_recall, which require a
                ``ground_truth`` on every case.
            test_cases: Override the built-in FAB test set. Each item needs a
                ``question`` key; ``ground_truth`` is required only when
                ``reference_free`` is False.
            limit: Run only the first ``limit`` test cases. ``None`` or a
                non-positive value runs the full set.
            top_k: Number of chunks to retrieve per question. Overrides the
                configured ``evaluation.top_k`` when set to a positive value.
        """
        self._patch_ragas_vertexai_import()

        from ragas.metrics import answer_relevancy, faithfulness

        cases = test_cases if test_cases is not None else TEST_CASES
        if limit is not None and limit > 0:
            cases = cases[:limit]

        if reference_free:
            # context_utilization is the no-reference variant of context_precision;
            # it isn't re-exported at the ragas.metrics top level, so import directly.
            from ragas.metrics._context_precision import context_utilization

            metrics = [faithfulness, answer_relevancy, context_utilization]
            thresholds = REFERENCE_FREE_THRESHOLDS
        else:
            from ragas.metrics import context_precision, context_recall

            metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
            thresholds = METRIC_THRESHOLDS

        eval_cfg = self._settings.evaluation if self._settings else None
        default_top_k = eval_cfg.top_k if eval_cfg else 3
        resolved_top_k = top_k if top_k is not None and top_k > 0 else default_top_k
        max_chars = eval_cfg.max_context_chars if eval_cfg else 800

        rows = []
        per_question = []
        for tc in cases:
            request = RetrieveRequest(query=tc["question"], generate_answer=True, top_k=resolved_top_k)
            response = await self._pipeline.retrieve(request)
            answer = await self._generator.generate(tc["question"], response.chunks)

            # Truncate contexts to stay within judge LLM token limits
            contexts = [c.text[:max_chars] for c in response.chunks]

            row = {
                "question": tc["question"],
                # Strip inline citations so faithfulness judges only real claims.
                "answer": _strip_citations(answer),
                "contexts": contexts,
            }
            # Reference-based metrics need a ground-truth column; reference-free
            # metrics ignore it, and test cases may not provide one.
            if not reference_free:
                row["ground_truth"] = tc["ground_truth"]
            rows.append(row)

            per_question.append({
                "question": tc["question"],
                "ground_truth": tc.get("ground_truth"),
                "answer": answer,
                "chunks_retrieved": len(response.chunks),
                "sources": list({c.source for c in response.chunks}),
            })
            logger.info("Test case complete", question=tc["question"][:60])

        metric_results = self._score_rows(rows, metrics, thresholds)
        logger.info("Evaluation complete", reference_free=reference_free, results=metric_results)
        return {
            "metrics": metric_results,
            "per_question": per_question,
            "reference_free": reference_free,
            "test_cases_count": len(cases),
            "top_k": resolved_top_k,
            "all_pass": all(v["pass"] for v in metric_results.values()),
        }
