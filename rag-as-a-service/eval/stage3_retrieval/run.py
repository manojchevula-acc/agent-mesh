"""Stage 3 CLI — retrieval quality and ordering.

    # One-time: bootstrap chunk-id judgments from the existing gold set, then review
    python -m eval.stage3_retrieval.run --derive-qrels

    # Normal run (retrieve + score)
    python -m eval.stage3_retrieval.run
    python -m eval.stage3_retrieval.run --id 13,14,15   # re-run a subset; others are kept
    python -m eval.stage3_retrieval.run --score-only    # re-score the last run, no retrieval

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
from .runner import AblationRunner

STAGE = scoring.STAGE


def build_parser() -> argparse.ArgumentParser:
    parser = base_parser(__doc__ or "")
    parser.add_argument(
        "--derive-qrels",
        action="store_true",
        help="Rebuild qrels.json from gold_qa.json by resolving expected_sources against "
        "the live index. Verified questions are never overwritten.",
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
    parser.add_argument(
        "--no-parity-check",
        action="store_true",
        help="Skip the per-question comparison against RetrievalPipeline.retrieve() "
        "(halves the query count, but stops detecting ablation drift).",
    )
    return parser


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()
    run_path = paths.run_file(f"{STAGE}_run")

    # ── Ground truth ──────────────────────────────────────────────────
    qrels = load_qrels(paths.qrels)
    if args.derive_qrels or qrels is None:
        gold = read_json(paths.gold_qa)
        if gold is None:
            raise SystemExit(
                f"No gold set at {paths.gold_qa}. Generate it with "
                "`python scripts/build_gold_set.py` first."
            )
        print(f"  loading collection: {settings.vectordb.collection_name}")
        index = await ChunkIndex.load(settings)
        qrels, warnings = derive(gold, index, qrels)
        save_qrels(paths.qrels, qrels)
        print(f"  derived {len(qrels.questions)} qrel question(s) -> {paths.qrels}")
        for warning in warnings:
            print(f"    WARN {warning}")
        print("  Review the judgments, prune ambiguous matches, then set verified: true.")

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
        ablation = AblationRunner(settings, embedder, vectordb, rank_depth=args.rank_depth)

        records: list[RetrievalRunRecord] = []
        for question in questions:
            record = await ablation.run(question, check_parity=not args.no_parity_check)
            final = record.stages["final"]
            hit = next(
                (h.rank + 1 for h in final.hits if h.chunk_id in question.relevant_ids), None
            )
            print(
                f"  done: {question.id:>3}  first_hit_rank={hit or 'MISS'}  "
                f"parity={record.pipeline_parity}"
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
                "rank_depth": ablation.rank_depth,
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
            "Graded-relevance scoring of the ordering production serves, plus a "
            "per-component ablation so a regression can be attributed to dense, sparse, "
            "fusion, reranking or the freshness penalty."
        ),
        meta={"run_file": str(run_path), "qrels": str(paths.qrels), **run.config},
    )
    scoring.score(run, qrels, report)
    return emit(report, args, paths)


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
