"""Atomic JSON/text persistence and id-keyed merging.

Every write goes through a temp file + ``os.replace`` so an interrupted run
(Ctrl-C during a 40-question generation pass is routine) can never leave a
half-written ground-truth or results file behind.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, TypeVar

T = TypeVar("T")


def read_json(path: Path, default: Any = None) -> Any:
    """Return parsed JSON at ``path``, or ``default`` when the file is absent."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> Path:
    """Atomically write ``data`` as indented UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_text(path: Path, text: str) -> Path:
    """Atomically write ``text`` as UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def merge_by_id(
    existing: Iterable[dict[str, Any]],
    new: Iterable[dict[str, Any]],
    order: list[str] | None = None,
    key: str = "id",
) -> list[dict[str, Any]]:
    """Overlay ``new`` records onto ``existing``, keyed by ``key``.

    Re-running a stage for a subset of ids (``--id 13,14``) must never discard
    results for the ids that were not re-run. When ``order`` is given the output
    follows it, which keeps diffs between runs readable.
    """
    merged: dict[str, dict[str, Any]] = {r[key]: r for r in existing}
    for record in new:
        merged[record[key]] = record
    if order:
        ordered = [merged[i] for i in order if i in merged]
        # Anything not in `order` (e.g. an id removed from the gold set) is kept
        # at the end rather than silently dropped.
        ordered.extend(v for k, v in merged.items() if k not in set(order))
        return ordered
    return list(merged.values())


def parse_id_args(raw: list[str] | None) -> list[str] | None:
    """Normalise repeated/comma-joined ``--id`` flags into a flat id list."""
    if not raw:
        return None
    return [part.strip() for value in raw for part in value.split(",") if part.strip()]
