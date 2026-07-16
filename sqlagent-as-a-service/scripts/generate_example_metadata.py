"""Metadata generation utility for the few-shot example seed set (Phase 3).

Computes the ``metadata`` block (tables, columns, joins, intent, sql_pattern,
aggregations, filters, business_terms, complexity — see
``sql_agent/memory/example_metadata.py``) for every entry in
``sql_agent/data/example_seed.yaml``, purely from each entry's own ``question`` +
``sql`` via the deterministic rule-based classifiers (no LLM call, so this is free and
safe to re-run any time the seed set changes).

Usage:
    uv run python scripts/generate_example_metadata.py                 # dry-run report
    uv run python scripts/generate_example_metadata.py --write         # rewrite the seed file
    uv run python scripts/generate_example_metadata.py --file path.yaml --write

Caveat (documented in docs/FEWSHOT_RETRIEVAL.md): ``--write`` regenerates the
``examples:`` list via a fresh YAML dump. The file's HEADER comment block (everything
before the top-level ``examples:`` key) is preserved verbatim, but per-entry
section-divider comments inside the list (e.g. ``# ---- Relationship Manager ----``)
are plain YAML comments with no data-model home and are NOT preserved — PyYAML has no
comment round-trip. Re-add them by hand afterward if you rely on them for navigation.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from sql_agent.memory.example_metadata import generate

DEFAULT_SEED = Path(__file__).resolve().parents[1] / "sql_agent" / "data" / "example_seed.yaml"

_TOP_LEVEL_KEY = re.compile(r"^examples:\s*\n", flags=re.MULTILINE)


def _split_header(text: str) -> tuple[str, dict]:
    """Return (preserved header text, parsed {"examples": [...]}) for ``text``."""
    match = _TOP_LEVEL_KEY.search(text)
    if not match:
        raise SystemExit("no top-level 'examples:' key found in the seed file")
    header = text[: match.start()]
    data = yaml.safe_load(text) or {}
    return header, data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=str(DEFAULT_SEED), help="path to the seed YAML")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the file with metadata added (default: dry-run report)")
    args = ap.parse_args()

    path = Path(args.file)
    text = path.read_text(encoding="utf-8")
    header, data = _split_header(text)
    rows = data.get("examples", [])
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: expected a top-level 'examples:' list")

    for row in rows:
        question = (row.get("question") or "").strip()
        sql = (row.get("sql") or "").strip()
        meta = generate(question, sql)
        row["metadata"] = meta
        print(f"{question[:70]:<70} | intent={meta['intent']:<24} "
              f"pattern={','.join(meta['sql_pattern']):<30} complexity={meta['complexity']}")

    print(f"\n{len(rows)} example(s) processed.")

    if args.write:
        dumped = yaml.safe_dump({"examples": rows}, sort_keys=False, allow_unicode=True,
                                 width=100, default_flow_style=False)
        path.write_text(header + dumped, encoding="utf-8")
        print(f"Wrote metadata into {path}")
    else:
        print("Dry-run (no file written) — pass --write to persist.")


if __name__ == "__main__":
    main()
