"""Diagnostic: show the per-statement breakdown behind a faithfulness /
answer-relevancy score.

This is a READ-ONLY mirror of what ``--ragas`` already does in stage 4. It imports the
*unmodified* stock RAGAS ``faithfulness`` and ``answer_relevancy`` metrics, wires them
to the same judge LLM and embeddings ``ragas_metrics`` uses, then prints the judge's
raw output: every decomposed statement, its 0/1 verdict, and the judge's own reason.
No examples, expected answers, or verdicts are baked in here — every number comes
from the live judge at runtime, for whatever question you pass.

Usage:
    # Generate the answer via the real RAG pipeline, then diagnose it:
    python -m eval.stage4_generation.diagnose_ragas \\
        --question "If FTP is 5.55% and the minimum spread is 145 bps, what is the minimum all-in rate?"

    # Diagnose an answer/contexts you already have (skips retrieval+generation):
    python -m eval.stage4_generation.diagnose_ragas \\
        --question "..." \\
        --answer "..." --context "first chunk text" --context "second chunk text"
"""

from __future__ import annotations

import argparse
import asyncio

from gernas_rag.config.settings import get_settings
from gernas_rag.embeddings.factory import get_embedder
from gernas_rag.llm.factory import get_llm
from gernas_rag.vectordb.factory import get_vectordb

from . import ragas_metrics
from .runner import GenerationRunner


def _rule(char: str = "-") -> None:
    print(char * 78)


async def diagnose(question: str, answer: str | None, contexts: list[str] | None) -> None:
    settings = get_settings()
    embedder = get_embedder(settings.embedding)

    if answer is None or contexts is None:
        vectordb = get_vectordb(settings.vectordb)
        llm = get_llm(settings.llm)
        runner = GenerationRunner(settings, embedder, vectordb, llm, top_k=settings.retrieval.final_top_k)
        record = await runner.run("diagnose", question)
        answer = record.answer
        contexts = [c.text for c in record.contexts]
        sources = [c.document or "?" for c in record.contexts]
    else:
        sources = ["(supplied by caller)"] * len(contexts)

    ragas_metrics._patch_ragas_vertexai_import()
    ragas_llm = ragas_metrics.make_ragas_llm(settings)
    ragas_emb = ragas_metrics.make_ragas_embeddings(settings, embedder)

    # --- echo the exact inputs being scored -------------------------------
    _rule("=")
    print("INPUTS TO THE METRICS")
    _rule("=")
    print(f"\nQUESTION:\n{question}\n")
    print(f"ANSWER:\n{answer}\n")
    print(f"RETRIEVED CONTEXTS ({len(contexts)} chunk(s)):")
    for i, (ctx, src) in enumerate(zip(contexts, sources)):
        print(f"\n  [chunk {i}] source={src} | {len(ctx)} chars")
        print("  " + ctx.replace("\n", "\n  "))
    print()

    # ----------------------------------------------------------------------
    # FAITHFULNESS — stock RAGAS, two real LLM calls (decompose, then judge).
    # ----------------------------------------------------------------------
    from ragas.metrics import answer_relevancy, faithfulness
    from ragas.metrics._answer_relevance import ResponseRelevanceInput

    faithfulness.llm = ragas_llm

    # RAGAS internal row schema: user_input / response / retrieved_contexts
    row = {
        "user_input": question,
        "response": answer,
        "retrieved_contexts": contexts,
    }

    _rule("=")
    print("FAITHFULNESS BREAKDOWN (stock RAGAS faithfulness)")
    _rule("=")

    stmt_out = await faithfulness._create_statements(row, callbacks=None)
    statements = stmt_out.statements
    print(f"\nStep 1 — answer decomposed into {len(statements)} statement(s):")
    for i, s in enumerate(statements):
        print(f"  {i + 1}. {s}")

    verdict_out = await faithfulness._create_verdicts(row, statements, callbacks=None)
    print("\nStep 2 — each statement judged against the retrieved context ONLY:")
    print("        (note: the question text is NOT part of the grounding here)\n")
    supported = 0
    for i, v in enumerate(verdict_out.statements):
        mark = "PASS" if v.verdict else "FAIL"
        supported += 1 if v.verdict else 0
        print(f"  [{mark}] verdict={v.verdict}  {v.statement}")
        print(f"         reason: {v.reason}\n")

    score = faithfulness._compute_score(verdict_out)
    print(f"Faithfulness = {supported}/{len(verdict_out.statements)} = {score:.3f}")

    # ----------------------------------------------------------------------
    # ANSWER RELEVANCY — reverse-generate questions, cosine vs. the original.
    # ----------------------------------------------------------------------
    answer_relevancy.llm = ragas_llm
    answer_relevancy.embeddings = ragas_emb

    _rule("=")
    print("ANSWER RELEVANCY BREAKDOWN (stock RAGAS answer_relevancy)")
    _rule("=")

    # NOTE: RAGAS asks for n=strictness completions in one call. Groq rejects
    # n>1 ('n must be at most 1'), so we loop n=1 to stay provider-agnostic.
    responses = []
    for _ in range(answer_relevancy.strictness):
        out = await answer_relevancy.question_generation.generate_multiple(
            data=ResponseRelevanceInput(response=answer),
            llm=ragas_llm,
            callbacks=None,
            n=1,
        )
        responses.extend(out)
    gen_questions = [r.question for r in responses]
    sims = answer_relevancy.calculate_similarity(question, gen_questions)
    print(f"\nReverse-generated {len(gen_questions)} question(s) from the answer,")
    print("then cosine similarity of each against your ORIGINAL question:\n")
    for q, sim, r in zip(gen_questions, sims, responses):
        print(f"  cos={sim:.3f} noncommittal={r.noncommittal}  -> {q}")
    all_noncommittal = all(r.noncommittal for r in responses)
    rel = float(sims.mean()) * (0 if all_noncommittal else 1)
    print(f"\nAnswer Relevancy = mean(cos)={sims.mean():.3f}"
          f"{' x 0 (all noncommittal)' if all_noncommittal else ''} = {rel:.3f}")

    _rule("=")
    print("READ-ME: A 'FAIL' verdict above only means the statement is not literally")
    print("inferable from the retrieved chunk. Values from the QUESTION and correct")
    print("arithmetic/unit-conversions show as FAIL because stock faithfulness never")
    print("sees the question and does not verify math. That is the metric limitation,")
    print("not a wrong answer.")
    _rule("=")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--question", required=True)
    p.add_argument("--answer", default=None, help="Skip generation; diagnose this answer.")
    p.add_argument(
        "--context",
        action="append",
        default=None,
        dest="contexts",
        help="Retrieved chunk text (repeat for multiple). Skips retrieval if set.",
    )
    args = p.parse_args()
    asyncio.run(diagnose(args.question, args.answer, args.contexts))


if __name__ == "__main__":
    main()
