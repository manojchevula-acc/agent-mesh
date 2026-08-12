"""Stage 1 — Extraction & layout fidelity.

Re-runs DoclingExtractor + DoclingImageExtractor over every PDF in the manifest
and asks one question: did extraction FIND everything a human confirmed is on
the page, on the right page?

This is upstream of every other stage. A figure Docling never emits cannot be
embedded, cannot be retrieved, and cannot be read by the VLM — but the symptom
surfaces four stages later as "the model didn't know", which is why the failure
is worth isolating here.

Page-level matching is deliberate: a heading detected on the wrong page still
counts as a miss, because captions.py resolves an image's context by locating
the nearest heading ON ITS PAGE. Wrong page => wrong caption => wrong stub.
"""

import asyncio
import html
import re
import sys
from collections import Counter

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.extraction.factory import get_extractor  # noqa: E402
from gernas_rag.images.factory import get_image_extractor  # noqa: E402
from gernas_rag.images.filters import ImageFilter  # noqa: E402
from gernas_rag.models.asset import ImageRole  # noqa: E402

from ..common.ground_truth import DocLayout, load_manifest  # noqa: E402
from ..common.text import normalize  # noqa: E402
from ..core import report  # noqa: E402


# Docling writes headings into raw_markdown as ATX markers. Docling also HTML-
# escapes the text ("Governance &amp; Escalation"), hence html.unescape below.
_MD_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _page_recall(expected: list[int], found: list[int]) -> tuple[int, int]:
    """Multiset recall over page numbers.

    A Counter, not a set: two figures on page 3 must be satisfied by two
    detections on page 3, not by one. Portfolio Risk p3 is exactly this case,
    and a set-based comparison would score it 100% while missing a figure.
    """
    want, have = Counter(expected), Counter(found)
    hit = sum(min(count, have.get(page, 0)) for page, count in want.items())
    return hit, sum(want.values())


def _heading_recall(expected: list[str], found: list[str]) -> tuple[int, list[str]]:
    """Substring-tolerant heading match.

    Docling frequently returns a heading with trailing numbering or a joined
    subtitle, so exact equality under-reports badly. Containment either way,
    over normalised text, is the honest middle ground.
    """
    pool = [normalize(h) for h in found if h]
    missed = []
    hit = 0
    for heading in expected:
        needle = normalize(heading)
        if any(needle in candidate or candidate in needle for candidate in pool):
            hit += 1
        else:
            missed.append(heading)
    return hit, missed


async def _extract_one(settings, doc: DocLayout, text_extractor, image_extractor) -> dict:
    """Extract one document with SHARED extractor instances.

    The extractors are passed in, not built here: each lazily caches a Docling
    DocumentConverter, and rebuilding one per document reloads the layout
    models every time — which exhausts memory and segfaults partway through a
    corpus this size.
    """
    extraction = await text_extractor.extract(doc.file)

    # Headings come from raw_markdown, NOT from extraction.elements.
    #
    # Two reasons, both load-bearing. (1) DoclingExtractor emits no
    # ElementType.HEADING at all for this corpus — every heading arrives typed
    # as PARAGRAPH — so scoring `elements` reports 0% regardless of extraction
    # quality. (2) More importantly, HierarchicalChunker reads
    # `extraction.raw_markdown` (hierarchical.py:56), so the markdown '#'
    # markers are what ACTUALLY drives the parent/child split. Measuring
    # anything else would score a field the pipeline never consumes.
    #
    # Matched by text only, not page: raw_markdown carries no page numbers, and
    # DoclingExtractor sets page_number=None on every element anyway (it never
    # reads item.prov — EVAL_SUITE_PLAN.md tracks that fix).
    headings = [
        html.unescape(m.group(1)).strip()
        for m in _MD_HEADING.finditer(getattr(extraction, "raw_markdown", "") or "")
    ]

    image_filter = ImageFilter(settings.multimodal.extraction)
    raws = [
        r
        for r in await image_extractor.extract_images(doc.file)
        if image_filter.evaluate(r).keep
    ]

    return {
        "headings": headings,
        "figure_pages": [r.page_number for r in raws if r.role is not ImageRole.TABLE_IMAGE],
        "table_pages": [r.page_number for r in raws if r.role is ImageRole.TABLE_IMAGE],
    }


def run(only: str | None = None) -> report.StageResult:
    settings = get_settings()
    docs = load_manifest()
    if only:
        wanted = {s.strip() for s in only.split(",")}
        docs = [d for d in docs if d.document in wanted]

    result = report.StageResult(
        stage="stage1",
        title="Stage 1 — Extraction & Layout Fidelity",
        context={
            "extractor": settings.chunking.extraction_strategy.value,
            "image_backend": settings.multimodal.extraction.backend.value,
            "documents": len(docs),
        },
    )

    verified = [d for d in docs if d.verified]
    if not verified:
        result.add("heading_recall", report.NA, "no verified documents in manifest")
        result.add("figure_recall", report.NA, "no verified documents in manifest")
        result.add("table_recall", report.NA, "no verified documents in manifest")
        result.add("page_accuracy", report.NA, "no verified documents in manifest")
        return result

    totals = {"h_hit": 0, "h_all": 0, "f_hit": 0, "f_all": 0, "t_hit": 0, "t_all": 0}

    # Built once and shared across documents — see _extract_one's docstring.
    text_extractor = get_extractor(settings.chunking, docs[0].file)
    image_extractor = get_image_extractor(
        settings.multimodal.extraction, settings.chunking, docs[0].file
    )

    for doc in docs:
        found = asyncio.run(_extract_one(settings, doc, text_extractor, image_extractor))

        if not doc.verified:
            result.rows.append(
                {"document": doc.document, "headings": "n/a", "figures": "n/a",
                 "tables": "n/a", "note": "unverified — not scored"}
            )
            continue

        h_hit, h_missed = _heading_recall(doc.headings, found["headings"])
        f_hit, f_all = _page_recall(doc.figure_pages, found["figure_pages"])
        t_hit, t_all = _page_recall(doc.table_pages, found["table_pages"])
        optional = len(doc.optional_figure_pages)

        totals["h_hit"] += h_hit
        totals["h_all"] += len(doc.headings)
        totals["f_hit"] += f_hit
        totals["f_all"] += f_all
        totals["t_hit"] += t_hit
        totals["t_all"] += t_all

        result.rows.append(
            {
                "document": doc.document,
                "headings": f"{h_hit}/{len(doc.headings)}",
                "figures": f"{f_hit}/{f_all}",
                "tables": f"{t_hit}/{t_all}",
                # Surfaced per-document so a relaxed bar is never invisible:
                # anyone reading the report can see the denominator was reduced.
                "excluded": optional or "",
                "note": ("missed: " + "; ".join(h_missed[:3])) if h_missed else "",
            }
        )

    def ratio(hit: str, all_: str) -> float | None:
        return totals[hit] / totals[all_] if totals[all_] else report.NA

    result.add("heading_recall", ratio("h_hit", "h_all"), f"{totals['h_hit']}/{totals['h_all']}")
    result.add("figure_recall", ratio("f_hit", "f_all"), f"{totals['f_hit']}/{totals['f_all']}")
    result.add("table_recall", ratio("t_hit", "t_all"), f"{totals['t_hit']}/{totals['t_all']}")

    # page_accuracy is folded into the recall numbers above: _page_recall
    # already matches per-page, so a right-count/wrong-page detection is
    # counted as a miss. Reporting it separately would double-count.
    result.add(
        "page_accuracy",
        ratio("f_hit", "f_all"),
        "figures+tables are matched per-page, so recall already encodes it",
    )

    excluded_total = sum(len(d.optional_figure_pages) for d in docs if d.verified)
    if excluded_total:
        result.notes.append(
            f"{excluded_total} figure region(s) carry must_detect=false and are "
            "excluded from figure_recall's denominator — regions this pipeline's "
            "ImageFilter is correct to drop (see each manifest entry's "
            "why_optional). They remain listed so the manifest still describes "
            "the page truthfully."
        )

    unverified = [d.document for d in docs if not d.verified]
    if unverified:
        result.notes.append(
            f"{len(unverified)} document(s) unverified and excluded from scoring: "
            + ", ".join(unverified)
        )
    return result
