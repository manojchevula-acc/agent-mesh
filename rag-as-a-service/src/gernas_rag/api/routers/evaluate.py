"""POST /api/v1/evaluate — run RAGAS evaluation over the FAB test set."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ...evaluation.evaluator import RAGEvaluator
from ...evaluation.test_dataset import TEST_CASES
from ...utils.logging import get_logger
from ..deps import verify_auth

logger = get_logger(__name__)
router = APIRouter()


class EvaluateAnswerRequest(BaseModel):
    """A single already-generated answer to score reference-free (no ground truth)."""

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    contexts: list[str] = Field(..., min_length=1)


@router.post("/evaluate")
async def run_evaluation(
    request: Request,
    reference_free: bool = False,
    limit: int | None = None,
    top_k: int | None = None,
    _: None = Depends(verify_auth),
) -> dict:
    """Run RAGAS evaluation over the FAB test cases.

    Uses the app's existing Qdrant/embedder/LLM connections — no second
    process needed. Takes 5-10 minutes on CPU.

    Set ``reference_free=true`` to evaluate without ground truth, using only
    faithfulness, answer_relevancy and context_utilization.

    ``limit`` caps how many test cases run (defaults to the full set). ``top_k``
    overrides how many chunks are retrieved per question.
    """
    pipeline = request.app.state.retrieval_pipeline
    generator = request.app.state.generator
    settings = request.app.state.settings
    embedder = request.app.state.embedder

    try:
        evaluator = RAGEvaluator(pipeline, generator, settings, embedder=embedder)
        results = await evaluator.run(
            reference_free=reference_free,
            limit=limit,
            top_k=top_k,
        )
    except Exception as exc:
        logger.error("Evaluation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return results


@router.post("/evaluate/answer")
async def evaluate_answer(
    request: Request,
    body: EvaluateAnswerRequest,
    _: None = Depends(verify_auth),
) -> dict:
    """Reference-free score for a single answer the user just received.

    Takes the question, the generated answer and the retrieved contexts (all
    available from a /retrieve call) and scores them with faithfulness,
    answer_relevancy and context_utilization — no ground truth required.
    Typically 10-30s since it judges one item.
    """
    pipeline = request.app.state.retrieval_pipeline
    generator = request.app.state.generator
    settings = request.app.state.settings
    embedder = request.app.state.embedder

    try:
        evaluator = RAGEvaluator(pipeline, generator, settings, embedder=embedder)
        return await evaluator.evaluate_answer(body.question, body.answer, body.contexts)
    except Exception as exc:
        logger.error("Single-answer evaluation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get("/evaluate/test-cases")
async def get_test_cases(
    _: None = Depends(verify_auth),
) -> dict:
    """Return the 7 FAB test questions and their ground truths."""
    return {"test_cases": TEST_CASES, "count": len(TEST_CASES)}
