"""Stage 3 CLI — retrieval quality and ordering.

    # Normal run: derive judgments from the gold answers, retrieve, score
    python -m eval.stage3_retrieval.run
    python -m eval.stage3_retrieval.run --id 13,14,15   # re-run a subset; others are kept
    python -m eval.stage3_retrieval.run --score-only    # re-score the last run, no retrieval
    python -m eval.stage3_retrieval.run --derive-only   # refresh qrels.json and stop

Relevance judgments are *derived* from ``gold_qa.json``'s expected answers on
every run — there is no review step and no ``verified`` flag to flip. See
``qrels.py`` for the grading rules and why deriving them from the gold answer
(rather than from retriever output) is not circular.

Retrieval is the expensive part (embedder + cross-encoder load); scoring is free,
so ``--score-only`` lets you iterate on metrics without re-running the pipeline.
"""

from __future__ import annotations

import argparse

from gernas_rag.config.settings import get_settings
from gernas_rag.embeddings.factory import get_embedder
from gernas_rag.vectordb.factory import get_vectordb

from ..core.corpus import ChunkIndex
from ..core.io import parse_id_args, read_json, write_json
from ..core.models import RetrievalRun, RetrievalRunRecord, StageReport
from ..core.runner import base_parser, emit, paths_from_args, run_stage
from . import scoring
from .qrels import derive, load_qrels, save_qrels
from .runner import RetrievalRunner

STAGE = scoring.STAGE


def build_parser() -> argparse.ArgumentParser:
    parser = base_parser(__doc__ or "")
    parser.add_argument(
        "--derive-only",
        action="store_true",
        help="Regenerate qrels.json from the gold answers and exit, without running "
        "retrieval. Useful for inspecting the judgments after a re-ingest.",
    )
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        default=None,
        help="Only run these question ids (comma-separated or repeated). Results for "
        "other ids already recorded are preserved.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N questions.")
    parser.add_argument(
        "--rank-depth",
        type=int,
        default=10,
        help="How deep to keep the reranked list, so recall@10 is measurable (default: 10). "
        "The first retrieval.final_top_k entries are what production serves.",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Score the existing run file without executing any retrieval.",
    )
    return parser


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()
    run_path = paths.run_file(f"{STAGE}_run")

    # ── Ground truth ──────────────────────────────────────────────────
    # Judgments are derived, not curated: regenerating on every run keeps them in
    # step with the live index for free. --score-only reuses the cached file so a
    # scoring change can be iterated on without touching the collection.
    if args.score_only:
        qrels = load_qrels(paths.qrels)
        if qrels is None:
            raise SystemExit(f"No qrels at {paths.qrels}; drop --score-only to derive them.")
    else:
        gold = read_json(paths.gold_qa)
        if gold is None:
            raise SystemExit(
                f"No gold set at {paths.gold_qa}. Generate it with "
                "`python scripts/build_gold_set.py` first."
            )
        print(f"  loading collection: {settings.vectordb.collection_name}")
        index = await ChunkIndex.load(settings)
        qrels, warnings = derive(gold, index)
        save_qrels(paths.qrels, qrels)
        graded = sum(1 for q in qrels.questions if q.relevant_ids)
        print(
            f"  graded {graded}/{len(qrels.questions)} question(s) against the gold "
            f"answers -> {paths.qrels}"
        )
        for warning in warnings:
            print(f"    WARN {warning}")
        if args.derive_only:
            return 0

    questions = qrels.questions
    ids = parse_id_args(args.ids)
    if ids:
        known = {q.id: q for q in questions}
        missing = [i for i in ids if i not in known]
        if missing:
            raise SystemExit(f"Unknown question id(s): {missing}")
        questions = [known[i] for i in ids]
    elif args.limit:
        questions = questions[: args.limit]

    # ── Retrieval ─────────────────────────────────────────────────────
    if args.score_only:
        raw = read_json(run_path)
        if raw is None:
            raise SystemExit(f"No recorded run at {run_path}; drop --score-only to create one.")
        run = RetrievalRun.model_validate(raw)
    else:
        print("  loading embedder / vector DB (this is the slow part)")
        embedder = get_embedder(settings.embedding)
        vectordb = get_vectordb(settings.vectordb)
        runner = RetrievalRunner(settings, embedder, vectordb, rank_depth=args.rank_depth)

        records: list[RetrievalRunRecord] = []
        for question in questions:
            record = await runner.run(question)
            hit = next(
                (h.rank + 1 for h in record.hits if h.chunk_id in question.relevant_ids), None
            )
            print(
                f"  done: {question.id:>3}  first_hit_rank={hit or 'MISS'}  "
                f"{record.latency_ms:.0f}ms"
            )
            records.append(record)

        previous = read_json(run_path)
        existing = RetrievalRun.model_validate(previous).records if previous else []
        merged = {r.id: r for r in existing}
        for record in records:
            merged[record.id] = record
        order = [q.id for q in qrels.questions]
        run = RetrievalRun(
            config={
                "collection": settings.vectordb.collection_name,
                "dense_top_k": settings.retrieval.dense_top_k,
                "sparse_top_k": settings.retrieval.sparse_top_k,
                "rrf_k": settings.retrieval.rrf_k,
                "pre_rerank_top_k": settings.retrieval.pre_rerank_top_k,
                "final_top_k": settings.retrieval.final_top_k,
                "rank_depth": runner.rank_depth,
                "embedding_model": settings.embedding.model_name,
                "freshness_penalty_enabled": settings.retrieval.freshness_penalty_enabled,
            },
            records=[merged[i] for i in order if i in merged],
        )
        write_json(run_path, run.model_dump(mode="json"))
        print(f"\n  run recorded -> {run_path}")

    # ── Scoring ───────────────────────────────────────────────────────
    report = StageReport(
        stage=STAGE,
        title="Stage 3 — Retrieval quality & ordering",
        summary=(
            "Graded-relevance scoring of the ordering production serves. Judgments are "
            "derived from the gold answers on every run; nothing here is hand-reviewed."
        ),
        meta={"run_file": str(run_path), "qrels": str(paths.qrels), **run.config},
    )
    scoring.score(run, qrels, report)
    return emit(report, args, paths)


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
