"""Shared CLI plumbing for every stage.

Each stage keeps its own domain arguments but inherits the same output contract:
a JSON result under ``data/eval/runs/``, a Markdown report under
``data/eval/reports/``, a console summary, and an exit code that CI can gate on.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from .io import write_json, write_text
from .models import StageReport
from .paths import EvalPaths
from .reporting import exit_code, render_console_summary, render_markdown


def configure_stdout() -> None:
    """Force UTF-8 on stdout so reports render on a default Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # Already-detached or non-tty stream.
                pass


def base_parser(description: str) -> argparse.ArgumentParser:
    """Argument parser preloaded with the flags every stage accepts."""
    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--eval-root",
        type=Path,
        default=None,
        help="Directory holding evaluation ground truth and outputs (default: data/eval).",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=None,
        help="Directory holding the source corpus documents (default: docs/).",
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, help="Override the JSON result path."
    )
    parser.add_argument(
        "--md-out", type=Path, default=None, help="Override the Markdown report path."
    )
    parser.add_argument(
        "--fail-on",
        choices=("gate", "error", "warn", "never"),
        default="gate",
        help="Strictness of the exit code (default: gate).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the console summary."
    )
    return parser


def paths_from_args(args: argparse.Namespace) -> EvalPaths:
    return EvalPaths.default(root=args.eval_root, docs_dir=args.docs_dir).ensure()


def emit(
    report: StageReport,
    args: argparse.Namespace,
    paths: EvalPaths,
    extra_json: dict[str, Any] | None = None,
) -> int:
    """Persist the report, print the summary and return the process exit code."""
    json_path = args.json_out or paths.run_file(report.stage)
    md_path = args.md_out or paths.report_file(report.stage)

    payload: dict[str, Any] = report.model_dump(mode="json")
    if extra_json:
        payload.update(extra_json)
    write_json(json_path, payload)
    write_text(md_path, render_markdown(report))

    if not args.quiet:
        print(render_console_summary(report))
        print(f"\nJSON:   {json_path}")
        print(f"Report: {md_path}")

    code = exit_code(report, args.fail_on)
    if not args.quiet:
        print(f"Exit:   {code} ({'PASS' if code == 0 else 'FAIL'}, --fail-on={args.fail_on})")
    return code


def run_stage(main: Callable[[argparse.Namespace], Awaitable[int]], args: argparse.Namespace) -> None:
    """Entry point: run an async stage main() and exit with its code."""
    configure_stdout()
    sys.exit(asyncio.run(main(args)))
