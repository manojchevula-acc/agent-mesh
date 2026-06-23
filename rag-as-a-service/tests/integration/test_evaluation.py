"""Integration test: evaluation dataset + retrieval/generation wiring.

The full RAGAS run requires an LLM judge and network access, so here we verify the
test dataset shape and that the retrieval+generation path produces an answer for a
test question using fakes.
"""

from gernas_rag.config.settings import Settings
from gernas_rag.evaluation.metrics import METRIC_THRESHOLDS
from gernas_rag.evaluation.test_dataset import TEST_CASES
from gernas_rag.generation.generator import ResponseGenerator
from gernas_rag.models.chunk import EmbeddedChunk
from gernas_rag.models.retrieval import RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline


def test_dataset_has_seven_cases():
    assert len(TEST_CASES) == 7
    for tc in TEST_CASES:
        assert tc["question"]
        assert tc["ground_truth"]


def test_metric_thresholds_present():
    assert set(METRIC_THRESHOLDS) == {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    }


async def test_retrieval_and_generation_for_test_question(
    fake_embedder, fake_vectordb, fake_llm, sample_chunk
):
    settings = Settings(_env_file=None, redis_enabled=False)  # type: ignore[call-arg]
    await fake_vectordb.upsert(
        [EmbeddedChunk(chunk=sample_chunk, dense_vector=[0.1] * 8, sparse_indices=[1], sparse_values=[0.5])]
    )
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    generator = ResponseGenerator(settings, fake_llm)

    request = RetrieveRequest(query=TEST_CASES[0]["question"], generate_answer=True)
    response = await pipeline.retrieve(request)
    answer = await generator.generate(request.query, response.chunks)

    assert response.total_results >= 1
    assert "FAKE-ANSWER" in answer
