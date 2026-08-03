"""GERNAS RAG — stage-wise evaluation suite for the multimodal pipeline.

The suite mirrors the ingest/serve pipeline one stage at a time, so a regression
can always be attributed to the stage that caused it rather than to the
end-to-end number:

    stage1_extraction   Did Docling find every figure / table / heading?
    stage2_enrichment   Are the VLM captions faithful, and are media chunks
                        stored and linked correctly?
    stage3_retrieval    Are the right chunks retrieved, in the right order?
    stage4_generation   Is the answer grounded, correct, cited and appropriately
                        abstaining?

Each stage is a separate CLI writing a machine-readable JSON result plus a
human-readable Markdown report, and exits non-zero when a gated metric misses
its threshold — so any stage can be wired into CI independently.

``src/`` is put on ``sys.path`` here when ``gernas_rag`` is not already
importable, so the suite runs from a plain checkout without an editable install
(the same convention ``scripts/`` already uses).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = REPO_ROOT / "src"


def _ensure_gernas_rag_importable() -> None:
    try:
        import gernas_rag  # noqa: F401
    except ModuleNotFoundError:
        if _SRC.is_dir() and str(_SRC) not in sys.path:
            sys.path.insert(0, str(_SRC))


_ensure_gernas_rag_importable()

__all__ = ["REPO_ROOT"]
