"""`python -m eval <stage>` — the single entry point for the evaluation suite.

    python -m eval                     list stages and their status
    python -m eval repair              fix imported-ground-truth encoding
    python -m eval remap               bind transcriptions to this pipeline's asset ids
    python -m eval stage1              extraction & layout fidelity
    python -m eval stage2a             index & artifact integrity
    python -m eval stage2b             vision perception fidelity
    python -m eval stage3              retrieval quality / ordering
    python -m eval stage4              answer quality (spans + judge)
    python -m eval all                 every implemented stage, in order

Exit code is 1 if any REQUIRED metric scored below its bar, so this is usable
as a CI gate. An n/a never fails the run — see core/report.py.
"""

import argparse
import subprocess
import sys

from .core import report
from .core.thresholds import ALL as ALL_BARS

# stage -> (module path, one-line description, implemented?)
STAGES = {
    "stage1": ("eval.stage1_extraction.check", "Extraction & layout fidelity", True),
    "stage2a": ("eval.stage2a_index.check", "Index & artifact integrity", True),
    "stage2b": ("eval.stage2b_vision.check", "Vision perception fidelity", True),
    "stage3": ("eval.stage3_retrieval.check", "Retrieval quality (RS / selection)", True),
    "stage4": ("eval.stage4_generation.check", "Answer quality (CS / generation)", True),
}


def _list() -> int:
    print("\nGERNAS RAG evaluation suite\n")
    print(f"  {'stage':<9} {'metrics':>7}  {'status':<14} description")
    print("  " + "-" * 74)
    for name, (_mod, description, implemented) in STAGES.items():
        count = len(ALL_BARS.get(name, {}))
        status = "ready" if implemented else "not implemented"
        print(f"  {name:<9} {count:>7}  {status:<14} {description}")
    print("\n  tools:")
    print("    repair    fix mojibake in the imported ground truth files")
    print("    remap     bind figure transcriptions to this pipeline's asset ids")
    print("\n  ground truth: data/eval/  ·  output: data/eval/runs/ + data/eval/reports/\n")
    return 0


def _run_stage(name: str, args) -> report.StageResult | None:
    module_path, _description, implemented = STAGES[name]
    if not implemented:
        print(f"\n{name} is not implemented yet — skipping.")
        return None

    import importlib

    module = importlib.import_module(module_path)
    if name == "stage2b":
        result = module.run(
            limit=args.limit, delay=args.delay, fresh=args.fresh, rescore=args.rescore
        )
    elif name == "stage1":
        result = module.run(only=args.only)
    elif name == "stage3":
        result = module.run(
            limit=args.limit, only=args.only, top_k=args.top_k,
            rank_depth=args.rank_depth, fresh=args.fresh,
        )
    elif name == "stage4":
        result = module.run(
            limit=args.limit, only=args.only, top_k=args.top_k,
            delay=args.delay, judge_model=args.judge_model, fresh=args.fresh,
            reuse_retrieval=args.reuse_retrieval,
            generate_top_k=args.generate_top_k,
        )
    else:
        result = module.run()

    report.print_summary(result)
    json_path, md_path = report.write(result)
    print(f"\n  -> {json_path}\n  -> {md_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m eval", add_help=True)
    parser.add_argument("target", nargs="?", help="stage name, a tool, or 'all'")
    parser.add_argument("--limit", type=int, help="stage2b: only the first N crops")
    parser.add_argument("--only", help="stage1: document names; stage3/4: gold case ids")
    parser.add_argument("--top-k", type=int, help="stage3/4: override retrieval.final_top_k")
    parser.add_argument(
        "--rank-depth", type=int, default=10,
        help="stage3: how deep to keep hits in data/eval/runs/stage3_retrieval.json "
             "(default 10) — does not change final_top_k or existing metrics",
    )
    parser.add_argument("--judge-model", help="stage4: judge model (must not be a generator)")
    parser.add_argument(
        "--fresh", action="store_true",
        help="stage2b/3/4: discard checkpointed cases and rescore from scratch",
    )
    parser.add_argument(
        "--rescore", action="store_true",
        help="stage2b: recompute scores from stored readings, no API calls",
    )
    parser.add_argument(
        "--generate-top-k", type=int,
        help="stage4: chunks actually passed to the model (<= --top-k). "
             "Models a VLM context budget without capping retrieval.",
    )
    parser.add_argument(
        "--reuse-retrieval", action="store_true",
        help="stage4: reuse the context stage3 stored instead of retrieving again",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between LLM calls")
    parser.add_argument("--dry-run", action="store_true", help="tools: show, do not write")
    args = parser.parse_args()

    if not args.target:
        return _list()

    if args.target == "repair":
        from .tools import repair_encoding

        return repair_encoding.run(dry_run=args.dry_run)

    if args.target == "remap":
        from .tools import remap_artifacts

        return remap_artifacts.run(dry_run=args.dry_run)

    if args.target == "all":
        return _run_all(args)

    if args.target not in STAGES:
        print(f"Unknown target: {args.target}")
        return _list() or 2

    try:
        result = _run_stage(args.target, args)
    except Exception as exc:  # noqa: BLE001 - report the stage, not a traceback
        print(f"\n{args.target} ERRORED: {exc}")
        return 1
    return 1 if (result and result.failed) else 0


def _run_all(args) -> int:
    """Run every stage, each in its OWN process.

    Not a loop in one interpreter, and this is not defensive over-engineering:
    running the stages in-process exhausts memory. Stage 1 holds Docling's
    layout models, stage 2a/3/4 add BGE-M3, SigLIP-2 and the cross-encoder
    reranker on top. Measured in-process, Docling threw `std::bad_alloc` on
    pages 10-11 of the pricing policy and stage 1 silently reported
    table_recall 92% instead of the 100% it scores standalone.

    A CI gate that quietly degrades under memory pressure is worse than one
    that fails: it reports a pipeline defect that does not exist. One process
    per stage keeps each stage's peak allocation independent.
    """
    forwarded = []
    for flag, value in (
        ("--limit", args.limit),
        ("--only", args.only),
        ("--top-k", args.top_k),
        ("--rank-depth", args.rank_depth),
        ("--delay", args.delay),
        ("--judge-model", args.judge_model),
    ):
        if value is not None:
            forwarded += [flag, str(value)]
    if args.fresh:
        forwarded.append("--fresh")

    failed, errored = [], []
    for name, (_mod, _desc, implemented) in STAGES.items():
        if not implemented:
            print(f"\n{name} is not implemented yet — skipping.")
            continue
        proc = subprocess.run([sys.executable, "-m", "eval", name, *forwarded])
        if proc.returncode == 1:
            failed.append(name)
        elif proc.returncode != 0:
            errored.append(f"{name} (exit {proc.returncode})")

    print("\n" + "=" * 78)
    print("SUITE SUMMARY")
    print(f"  below bar : {', '.join(failed) if failed else 'none'}")
    if errored:
        # A crashed stage is NOT a pass. Surfaced separately so a native crash
        # can never be mistaken for a clean run.
        print(f"  CRASHED   : {', '.join(errored)}")
    print(f"  reports   : data/eval/reports/  ·  json: data/eval/runs/")
    return 1 if (failed or errored) else 0


if __name__ == "__main__":
    sys.exit(main())
