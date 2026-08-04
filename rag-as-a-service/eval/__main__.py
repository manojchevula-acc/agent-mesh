"""Single entry point for the evaluation suite.

    python -m eval                       # list the stages
    python -m eval stage1 --init-manifest
    python -m eval stage2a
    python -m eval stage2b --init
    python -m eval stage3
    python -m eval stage4 --judge
    python -m eval all                   # every stage in order; exits non-zero if any fail

Each alias forwards its remaining arguments verbatim to the stage's own CLI, so
``python -m eval stage3 --help`` shows the stage's real options.
"""

from __future__ import annotations

import asyncio
import sys

from .core.runner import configure_stdout

STAGES: dict[str, tuple[str, str]] = {
    "stage1": ("eval.stage1_extraction.run", "Extraction & layout fidelity (reads the PDFs)"),
    "stage2a": ("eval.stage2_enrichment.run_integrity", "Index & artifact integrity"),
    "stage2b": ("eval.stage2_enrichment.run_captions", "VLM caption fidelity"),
    "stage3": ("eval.stage3_retrieval.run", "Retrieval quality & ordering"),
    "stage4": ("eval.stage4_generation.run", "Answer quality"),
}

# Ordered cheapest-and-most-fundamental first: a stage 2a failure explains a
# stage 3 failure, so running them in this order makes the first failure the
# informative one.
ALL_ORDER = ("stage2a", "stage2b", "stage3", "stage4")


def _usage() -> int:
    print(__doc__)
    print("Stages:")
    width = max(len(name) for name in STAGES)
    for name, (module, description) in STAGES.items():
        print(f"  {name.ljust(width)}  {description}")
    print(f"  {'all'.ljust(width)}  Run {', '.join(ALL_ORDER)} in order")
    print("\nStage 1 is excluded from 'all' because it re-extracts every PDF (minutes).")
    return 2


def _dispatch(alias: str, argv: list[str]) -> int:
    import importlib

    module = importlib.import_module(STAGES[alias][0])
    args = module.build_parser().parse_args(argv)
    return asyncio.run(module.main(args))


def main() -> int:
    configure_stdout()
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        return _usage()

    alias, argv = sys.argv[1], sys.argv[2:]

    if alias == "all":
        failures: list[str] = []
        for name in ALL_ORDER:
            print(f"\n{'=' * 70}\n{name}: {STAGES[name][1]}\n{'=' * 70}")
            try:
                code = _dispatch(name, list(argv))
            except SystemExit as exc:  # A stage refusing to start (missing ground truth).
                print(f"{name}: {exc}")
                code = 2
            if code != 0:
                failures.append(name)
        print(f"\n{'=' * 70}")
        print("FAILED: " + ", ".join(failures) if failures else "All stages passed.")
        return 1 if failures else 0

    if alias not in STAGES:
        print(f"Unknown stage {alias!r}\n")
        return _usage()
    return _dispatch(alias, argv)


if __name__ == "__main__":
    sys.exit(main())
