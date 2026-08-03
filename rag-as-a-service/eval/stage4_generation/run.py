"""Stage 4 CLI — answer correctness, grounding, attribution and abstention.

    # Generate + judge + score
    python -m eval.stage4_generation.run --judge

    # Iterate on the judge prompt without re-generating (cheap loop)
    python -m eval.stage4_generation.run --score-only --judge

    # A/B the multimodal path
    python -m eval.stage4_generation.run --hydration on  --run-name hydrated --judge
    python -m eval.stage4_generation.run --hydration off --run-name textonly --judge
    python -m eval.stage4_generation.run --score-only --run-name hydrated --compare-run textonly

Generation is expensive and judging is cheap, so they are separate flags: record
answers once, re-judge as often as the prompt changes.
"""

from __future__ import annotations

import argparse

from gernas_rag.config.settings import get_settings
from gernas_rag.embeddings.factory import get_embedder
from gernas_rag.llm.factory import get_llm
from gernas_rag.vectordb.factory import get_vectordb

from ..core.corpus import ChunkIndex
from ..core.io import parse_id_args, read_json, write_json
from ..core.models import GenerationRun, GenerationRunRecord, StageReport, TranscriptionSet
from ..core.runner import base_parser, emit, paths_from_args, run_stage
from . import scoring
from .judge import AnswerJudge, Judgment
from .runner import GenerationRunner

STAGE = scoring.STAGE

_HYDRATION_CHOICES = {"auto": None, "on": True, "off": False}


def build_parser() -> argparse.ArgumentParser:
    parser = base_parser(__doc__ or "")
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        default=None,
        help="Only run these gold ids (comma-separated or repeated). Others are preserved.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N questions.")
    parser.add_argument("--top-k", type=int, default=None, help="Chunks per question (default: retrieval.final_top_k).")
    parser.add_argument(
        "--hydration",
        choices=sorted(_HYDRATION_CHOICES),
        default="auto",
        help="'auto' follows configuration; 'on'/'off' override it for an A/B (default: auto).",
    )
    parser.add_argument(
        "--run-name",
        default="default",
        help="Names the run file, so hydration-on and hydration-off runs can coexist.",
    )
    parser.add_argument(
        "--score-only", action="store_true", help="Score an existing run without generating."
    )
    parser.add_argument(
        "--judge", action="store_true", help="Grade answers with the LLM judge before scoring."
    )
    parser.add_argument(
        "--judge-concurrency", type=int, default=3, help="Parallel judge calls (default: 3)."
    )
    parser.add_argument(
        "--compare-run",
        default=None,
        help="Name of another run to A/B this one against (e.g. the hydration-off run).",
    )
    return parser


def _run_key(name: str) -> str:
    return f"{STAGE}_run_{name}"


def _judgments_key(name: str) -> str:
    return f"{STAGE}_judgments_{name}"


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()

    gold = read_json(paths.gold_qa)
    if gold is None:
        raise SystemExit(
            f"No gold set at {paths.gold_qa}. Generate it with "
            "`python scripts/build_gold_set.py` first."
        )
    gold = [dict(item, id=str(item["id"])) for item in gold]

    selected = gold
    ids = parse_id_args(args.ids)
    if ids:
        known = {item["id"]: item for item in gold}
        missing = [i for i in ids if i not in known]
        if missing:
            raise SystemExit(f"Unknown gold id(s): {missing}")
        selected = [known[i] for i in ids]
    elif args.limit:
        selected = gold[: args.limit]

    run_path = paths.run_file(_run_key(args.run_name))
    judgments_path = paths.run_file(_judgments_key(args.run_name))

    # ── Generation ────────────────────────────────────────────────────
    if args.score_only:
        raw = read_json(run_path)
        if raw is None:
            raise SystemExit(f"No recorded run at {run_path}; drop --score-only to create one.")
        run = GenerationRun.model_validate(raw)
    else:
        print("  loading embedder / vector DB / LLM")
        embedder = get_embedder(settings.embedding)
        vectordb = get_vectordb(settings.vectordb)
        llm = get_llm(settings.llm)
        try:
            index = await ChunkIndex.load(settings)
        except Exception as exc:  # noqa: BLE001 — chunk ids are a nicety, not a requirement.
            print(f"  WARN could not load chunk index ({exc}); chunk_ids will be blank")
            index = None

        runner = GenerationRunner(
            settings,
            embedder,
            vectordb,
            llm,
            top_k=args.top_k or settings.retrieval.final_top_k,
            force_hydration=_HYDRATION_CHOICES[args.hydration],
            chunk_index=index,
        )
        config = runner.config_snapshot()
        print(f"  hydration: {'on' if config['hydration_enabled'] else 'off'} ({args.hydration})")

        records: list[GenerationRunRecord] = []
        for item in selected:
            record = await runner.run(item["id"], item["question"])
            print(
                f"  done: {record.id:>3}  chunks={len(record.contexts)}  "
                f"vision={'yes' if record.vision_used else 'no'}  "
                f"{record.retrieval_latency_ms + record.generation_latency_ms:.0f}ms"
            )
            records.append(record)

        previous = read_json(run_path)
        merged = {r.id: r for r in (GenerationRun.model_validate(previous).records if previous else [])}
        for record in records:
            merged[record.id] = record
        order = [item["id"] for item in gold]
        run = GenerationRun(
            config=config, records=[merged[i] for i in order if i in merged]
        )
        write_json(run_path, run.model_dump(mode="json"))
        print(f"\n  run recorded -> {run_path}")

    # ── Judging ───────────────────────────────────────────────────────
    stored = read_json(judgments_path, default={}) or {}
    judgments = {
        qid: Judgment(qid, data["verdict"], data.get("reason", ""), data.get("raw", ""))
        for qid, data in stored.items()
    }
    if args.judge:
        gold_by_id = {item["id"]: item for item in gold}
        by_id = run.by_id()
        targets = [
            (item["id"], item["question"], item.get("expected_answer", ""), by_id[item["id"]].answer)
            for item in selected
            if item["id"] in by_id and item.get("answerable", True)
        ]
        if targets:
            print(f"\n  judging {len(targets)} answer(s)")
            judge = AnswerJudge(get_llm(settings.llm), max_concurrent=args.judge_concurrency)
            for judgment in await judge.judge_all(targets):
                judgments[judgment.question_id] = judgment
                name = gold_by_id[judgment.question_id].get("name", "")
                print(f"    {judgment.question_id:>3} {judgment.verdict:<18} {name}")
            write_json(
                judgments_path,
                {
                    qid: {"verdict": j.verdict, "reason": j.reason, "raw": j.raw}
                    for qid, j in sorted(judgments.items())
                },
            )
            print(f"  judgments recorded -> {judgments_path}")

    # ── Scoring ───────────────────────────────────────────────────────
    raw_transcriptions = read_json(paths.figure_transcriptions)
    transcriptions = (
        TranscriptionSet.model_validate(raw_transcriptions) if raw_transcriptions else None
    )

    report = StageReport(
        stage=STAGE,
        title=f"Stage 4 — Answer quality ({args.run_name})",
        summary=(
            "Correctness, grounding, attribution and abstention scored independently, so a "
            "failure is attributable. Grounding uses human figure transcriptions where they "
            "exist, which is what makes it capable of catching a caption error."
        ),
        meta={"run_file": str(run_path), "run_name": args.run_name, **run.config},
    )
    scoring.score(run, gold, judgments, transcriptions, report)

    if args.compare_run:
        other_raw = read_json(paths.run_file(_run_key(args.compare_run)))
        if other_raw is None:
            report.add_finding(
                "warn", "COMPARE_RUN_MISSING", args.compare_run, "No such recorded run."
            )
        else:
            scoring.compare_runs(GenerationRun.model_validate(other_raw), run, report)

    return emit(report, args, paths)


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
