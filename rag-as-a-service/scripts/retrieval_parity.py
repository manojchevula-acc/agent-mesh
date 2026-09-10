"""Prove that RetrievalPipeline.retrieve() returns the ranking stage 3 recorded.

Stage 3 does NOT exercise the served retrieval path. Its runner
(``eval/stage3_retrieval/runner.py:81-95``) drives ``_searcher`` / ``_reranker`` /
``_freshness`` directly and never calls ``retrieve()``, so the MAF workflow is
built and never run. That makes stage 3 a check on the *components*, not on the
orchestration that sequences them.

This script closes that gap without an LLM, a judge, or a second checkout. It
replays the gold questions through the real ``retrieve()`` — encode, hybrid
search, rerank, freshness, assemble, all six executors — and diffs the result,
position by position, against a recorded stage 3 run.

    python -u scripts/retrieval_parity.py
    python -u scripts/retrieval_parity.py --baseline data/eval_last_run/runs/stage3_retrieval_run.json
    python -u scripts/retrieval_parity.py --id 6,22,30 --verbose

Exit code is 0 only when every question matches on every position.

A full pass is ~110 seconds per question on CPU (the cross-encoder dominates), so
``--out`` is rewritten after *every* question rather than once at the end — kill
the run at question 30 and the first 29 verdicts are still on disk. The file
carries ``"status": "in_progress"`` until the last question lands, so a partial
result can never be mistaken for a complete one. Run with ``python -u`` to get
per-question progress on stdout; without it the output is block-buffered and
nothing appears until the process exits.

WHY THE TWO PATHS ARE COMPARABLE
--------------------------------
Stage 3 reranks to ``rank_depth`` (10) and then applies freshness to that list.
``retrieve()`` reranks to ``request.top_k`` and then applies freshness to *that*
list. Freshness re-sorts by the penalised score, so applying it to 10 candidates
and taking the first 3 is NOT generally the same as applying it to 3. The two are
only comparable at equal depth, so this script sends ``top_k = rank_depth`` read
from the baseline's own config rather than assuming 10.

``include_parent=False`` because stage 3 skips parent expansion. It attaches
``parent_text`` without reordering, so it cannot move a position, but sending
False keeps the comparison free of an irrelevant vector-DB round trip.

WHAT IS COMPARED, AND WHAT IS NOT
---------------------------------
``RetrievedChunk`` carries no ``chunk_id``, so identity is established with
(source, clause_reference, modality, text) — the full chunk text, not a prefix.
``score`` is compared separately, so an ordering break and a scoring drift are
reported as different failures rather than one blended one.

``latency_ms`` is never compared: it differs by design.

A NOTE ON COMPARING ACROSS TIME
-------------------------------
The freshness penalty is computed against ``datetime.now()``
(``retrieval/freshness.py:29``), so scores drift as documents age past
``freshness_max_age_days``. Diff against a baseline recorded the *same day*
whenever scores matter. To reach further back, chain the comparison: today's
stage 3 run being byte-identical to an older one makes that older one a valid
transitive baseline.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import DocumentFilter, RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

# The frozen, checksummed, version-controlled baseline — see eval/baselines/README.md.
# Deliberately NOT under data/, which is gitignored and is overwritten by ordinary eval
# runs: a gate whose reference can be silently rewritten, or which is missing from a
# fresh clone, is not a gate.
DEFAULT_BASELINE = Path("eval/baselines/stage3_retrieval_run.json")


def _identity(source: str, clause: str, modality: str, text: str) -> tuple[str, str, str, str]:
    """What makes two chunks the same chunk, in the absence of a chunk_id."""
    return (source or "", clause or "", modality or "text", text or "")


def _baseline_identity(hit: dict) -> tuple[str, str, str, str]:
    return _identity(
        hit.get("document", ""),
        hit.get("clause_reference", ""),
        hit.get("modality", "text"),
        hit.get("text", ""),
    )


def _served_identity(chunk) -> tuple[str, str, str, str]:
    return _identity(chunk.source, chunk.clause_reference, chunk.modality, chunk.text)


def _short(identity: tuple[str, str, str, str]) -> str:
    source, clause, modality, text = identity
    label = clause or text[:40].replace("\n", " ")
    return f"{source or '?'}[{modality}]:{label}"


def compare(question_id: str, baseline_hits: list[dict], served) -> dict:
    """Diff one question. Returns a record describing what, if anything, moved."""
    result: dict = {"id": question_id, "status": "MATCH", "reasons": [], "detail": []}

    if len(baseline_hits) != len(served):
        result["status"] = "FAIL"
        result["reasons"].append(f"depth {len(baseline_hits)} -> {len(served)}")

    for position in range(min(len(baseline_hits), len(served))):
        want = _baseline_identity(baseline_hits[position])
        got = _served_identity(served[position])
        if want != got:
            result["status"] = "FAIL"
            if "ordering" not in result["reasons"]:
                result["reasons"].append("ordering")
            result["detail"].append(
                f"    rank {position}: expected {_short(want)}\n"
                f"             got      {_short(got)}"
            )

    # Scores are only meaningful where the chunks themselves line up.
    worst = 0.0
    for position in range(min(len(baseline_hits), len(served))):
        if _baseline_identity(baseline_hits[position]) != _served_identity(served[position]):
            continue
        delta = abs(float(baseline_hits[position]["score"]) - float(served[position].score))
        worst = max(worst, delta)
    if worst > 0.0:
        result["status"] = "FAIL"
        result["reasons"].append(f"score drift {worst:.3e}")
    result["max_score_delta"] = worst
    return result


def write_report(
    path: Path,
    baseline_path: Path,
    baseline: dict,
    rank_depth: int,
    results: list[dict],
    expected: int,
) -> None:
    """Persist progress so far.

    Called after every question, not once at the end: a full pass is over an hour
    and an interrupted run should not throw away the verdicts it already earned.

    Written to a temp file and moved into place, because this is rewritten every
    ~110 seconds and something is likely to be reading it — ``Path.replace`` is
    atomic on the same filesystem, so a reader sees either the previous complete
    file or the new one, never a truncated one.

    ``status`` is what makes a partial file safe to leave lying around: anything
    other than ``complete`` means questions are still missing, and the summary
    counts describe only what was compared.
    """
    failed = [r for r in results if r["status"] != "MATCH"]
    payload = {
        "baseline": str(baseline_path),
        "baseline_generated_at": baseline.get("generated_at"),
        "top_k": rank_depth,
        "status": "complete" if len(results) >= expected else "in_progress",
        "compared": len(results),
        "expected": expected,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "identical": len(results) - len(failed),
        "differing": len(failed),
        "differing_ids": [r["id"] for r in failed],
        "max_score_delta": max((r["max_score_delta"] for r in results), default=0.0),
        "questions": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


async def main(args: argparse.Namespace) -> int:
    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        print(f"No baseline run at {baseline_path}. Run `python -m eval stage3` first.")
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    config = baseline.get("config", {})
    records = baseline.get("records", [])

    # Read the depth from the baseline rather than assuming it — see module docstring.
    rank_depth = int(config.get("rank_depth") or 10)
    if rank_depth > 20:  # RetrieveRequest.top_k is capped at 20.
        print(f"Baseline rank_depth={rank_depth} exceeds RetrieveRequest's top_k cap of 20.")
        return 2

    if args.ids:
        wanted = {i.strip() for arg in args.ids for i in arg.split(",") if i.strip()}
        unknown = wanted - {str(r["id"]) for r in records}
        if unknown:
            print(f"Unknown question id(s): {sorted(unknown)}")
            return 2
        records = [r for r in records if str(r["id"]) in wanted]

    settings = get_settings()
    print(f"  baseline:   {baseline_path}  (recorded {baseline.get('generated_at')})")
    print(f"  collection: {settings.vectordb.collection_name}")
    print(f"  comparing {len(records)} question(s) at top_k={rank_depth}")

    if config.get("collection") and config["collection"] != settings.vectordb.collection_name:
        print(
            f"  REFUSING: baseline was recorded against collection "
            f"{config['collection']!r}, settings point at "
            f"{settings.vectordb.collection_name!r}."
        )
        return 2

    print("  loading embedder / vector DB (this is the slow part)")
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    # Constructing this builds the MAF workflow; retrieve() below runs it.
    pipeline = RetrievalPipeline(settings, embedder, vectordb)

    out_path = Path(args.out) if args.out else None
    if out_path:
        print(f"  progress -> {out_path} (rewritten after every question)")

    results: list[dict] = []
    for position, record in enumerate(records, start=1):
        response = await pipeline.retrieve(
            RetrieveRequest(
                query=record["question"],
                filters=DocumentFilter(),
                top_k=rank_depth,
                include_parent=False,
            )
        )
        outcome = compare(str(record["id"]), record["hits"], response.chunks)
        results.append(outcome)
        flag = "ok " if outcome["status"] == "MATCH" else "DIFF"
        reasons = f"  ({', '.join(outcome['reasons'])})" if outcome["reasons"] else ""
        print(
            f"  [{position:>2}/{len(records)}] {flag} {outcome['id']:>3}  "
            f"{response.latency_ms:>8.0f}ms{reasons}"
        )
        if args.verbose and outcome["detail"]:
            print("\n".join(outcome["detail"]))
        if out_path:
            write_report(out_path, baseline_path, baseline, rank_depth, results, len(records))

    failed = [r for r in results if r["status"] != "MATCH"]
    worst = max((r["max_score_delta"] for r in results), default=0.0)

    print()
    print("=" * 66)
    print(f"  questions compared     {len(results)}")
    print(f"  identical              {len(results) - len(failed)}")
    print(f"  differing              {len(failed)}")
    print(f"  max score delta        {worst:.6e}")
    print("=" * 66)

    if out_path:
        # Also covers the no-questions-selected case, which never entered the loop.
        write_report(out_path, baseline_path, baseline, rank_depth, results, len(records))
        print(f"  detail -> {out_path}")

    if failed:
        print(f"\n  RETRIEVAL PARITY FAILED for id(s): {', '.join(r['id'] for r in failed)}")
        print("  Re-run with --verbose to see which rank moved.")
        return 1

    print("\n  RETRIEVAL PARITY HELD — the served path reproduces the recorded ranking.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help=f"Recorded stage 3 run to diff against (default: {DEFAULT_BASELINE}).",
    )
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        default=None,
        help="Only compare these question ids (comma-separated or repeated).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the expected/actual chunk at every position that moved.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the per-question diff to this JSON file, rewritten after every "
        "question so an interrupted run keeps its results. Carries "
        '"status": "in_progress" until the last question completes.',
    )
    return parser


if __name__ == "__main__":
    sys.exit(asyncio.run(main(build_parser().parse_args())))
