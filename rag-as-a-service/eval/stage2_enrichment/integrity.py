"""Index integrity: storage, identity, mapping and linkage of every chunk.

The reconciliation check is the reason this module exists. In the ingest path a
figure's element text is forced to ``""`` by the extractor, the artifact ref is
written unconditionally after captioning, and the chunker skips any media element
whose text is empty. A VLM failure therefore stores the image and then drops the
chunk, while the ingestion log still counts the element as enriched. The only
way to see it is to reconcile artifacts against chunks, which is what
``check_orphan_artifacts`` does.
"""

from __future__ import annotations

import re
from pathlib import Path

from gernas_rag.config.settings import Settings
from gernas_rag.storage.artifact_store import BaseArtifactStore, LocalArtifactStore
from gernas_rag.utils.hashing import make_chunk_id

from ..core.corpus import ChunkIndex
from ..core.models import StageReport
from ..core.reporting import build_metric, rate, table
from ..core.text import looks_truncated
from ..core.thresholds import STAGE2_INTEGRITY

STAGE = "stage2_integrity"

# clause_reference values that are almost certainly a transcribed chart value
# rather than a clause number: 2+ decimal places, or a bare percentage-looking
# number. Media clause refs are derived from caption text, so these leak in.
_IMPLAUSIBLE_CLAUSE = re.compile(r"^\d+\.\d{2,}$")
# The chunker's fallback when no clause could be derived at all.
_FALLBACK_CLAUSE = re.compile(r"^(figure|table|page_image)(\s+p\.\d+)?$", re.IGNORECASE)

_MIN_CHILD_CHARS = 200
_MIN_PARENT_CHARS = 500


async def check_artifacts(
    index: ChunkIndex, store: BaseArtifactStore, report: StageReport
) -> tuple[int, int]:
    """Every media chunk's ``artifact_ref`` must resolve and decode as an image."""
    media = index.media
    resolvable = 0
    for chunk in media:
        if not chunk.artifact_ref:
            report.add_finding(
                "error",
                "MEDIA_CHUNK_WITHOUT_ARTIFACT",
                chunk.chunk_id,
                f"{chunk.document}: modality={chunk.modality} but no artifact_ref, so the "
                "source image can never be hydrated or shown as a citation.",
            )
            continue
        try:
            data, _ = await store.get_bytes(chunk.artifact_ref)
        except Exception as exc:  # noqa: BLE001 — a missing artifact is a finding, not a crash.
            report.add_finding(
                "error",
                "ARTIFACT_UNRESOLVABLE",
                chunk.artifact_ref,
                f"{chunk.document}: get_bytes failed ({exc}). Hydration will silently "
                "degrade this chunk to text-only.",
            )
            continue
        if not _decodes_as_image(data):
            report.add_finding(
                "error",
                "ARTIFACT_NOT_AN_IMAGE",
                chunk.artifact_ref,
                f"{chunk.document}: stored bytes did not decode as an image ({len(data)} bytes).",
            )
            continue
        resolvable += 1

    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "artifact_resolvable_rate",
            rate(resolvable, len(media)),
            detail=f"{resolvable}/{len(media)} media chunks resolve to a decodable image",
        )
    )
    return resolvable, len(media)


def _decodes_as_image(data: bytes) -> bool:
    try:
        from PIL import Image
    except ImportError:  # Pillow is a declared dependency; degrade rather than fail.
        return bool(data)
    import io

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def check_orphan_artifacts(
    index: ChunkIndex, store: BaseArtifactStore, report: StageReport
) -> int:
    """Artifacts on disk with no corresponding chunk — silently dropped figures.

    Only meaningful for a store that can be enumerated; a remote object store
    would need its own listing implementation, so this reports 'not measured'
    rather than a misleading zero.
    """
    if not isinstance(store, LocalArtifactStore):
        report.add_finding(
            "info",
            "ORPHAN_SCAN_SKIPPED",
            type(store).__name__,
            "Artifact store cannot be enumerated, so dropped-figure detection did not run.",
        )
        report.add_metric(build_metric(STAGE2_INTEGRITY, "orphan_artifact_count", None, unit="count"))
        return 0

    root = Path(store._root)  # noqa: SLF001 — no public accessor; read-only use.
    if not root.is_dir():
        report.add_metric(build_metric(STAGE2_INTEGRITY, "orphan_artifact_count", None, unit="count"))
        return 0

    indexed_refs = set(index.by_artifact_ref)
    orphans: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        ref = f"sha256:{path.stem}{path.suffix.lower()}"
        if ref not in indexed_refs:
            orphans.append(ref)
            report.add_finding(
                "error",
                "ORPHAN_ARTIFACT",
                ref,
                "An image was captured and stored but no chunk references it. This is the "
                "silent drop path: the extractor blanks figure text, enrichment sets "
                "artifact_ref even when the VLM fails, and the chunker skips elements with "
                "empty text — so the figure is absent from the index while ingestion "
                "reported it as enriched.",
            )

    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "orphan_artifact_count",
            float(len(orphans)),
            unit="count",
            detail=f"{len(orphans)} of {len(list(root.glob('*.png')))} stored images have no chunk",
        )
    )
    return len(orphans)


def check_identity_and_mapping(index: ChunkIndex, settings: Settings, report: StageReport) -> None:
    """Chunk ids, atomicity, and the metadata that makes a media chunk citable."""
    media = index.media

    # ── Deterministic id derivation ───────────────────────────────────
    # Re-deriving the id proves the document/modality/artifact mapping stored on
    # the chunk is the one it was actually keyed by, which is what makes
    # re-ingestion idempotent instead of duplicating every figure.
    derived_ok = 0
    for chunk in media:
        if not chunk.artifact_ref:
            continue
        expected = make_chunk_id(chunk.document, f"{chunk.modality}:{chunk.artifact_ref}")
        if expected == chunk.chunk_id:
            derived_ok += 1
        else:
            report.add_finding(
                "error",
                "CHUNK_ID_MISMATCH",
                chunk.chunk_id,
                f"{chunk.document}: id does not re-derive from "
                f"(document, modality, artifact_ref); re-ingestion will create a duplicate "
                "rather than updating this chunk.",
            )
    with_ref = [c for c in media if c.artifact_ref]
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "media_chunk_id_derivation_rate",
            rate(derived_ok, len(with_ref)),
            detail=f"{derived_ok}/{len(with_ref)} media chunk ids re-derive correctly",
        )
    )

    # ── Atomicity: one chunk per image ────────────────────────────────
    # Row-group splitting is opt-in; while it is off, more than one chunk per
    # artifact means a caption was fragmented and a figure's numbers are split
    # across chunks that will not be retrieved together.
    atomic_expected = settings.enrichment.max_media_chunk_tokens == 0
    atomic = sum(1 for chunks in index.by_artifact_ref.values() if len(chunks) == 1)
    if atomic_expected:
        for ref, chunks in index.by_artifact_ref.items():
            if len(chunks) > 1:
                report.add_finding(
                    "error",
                    "MEDIA_NOT_ATOMIC",
                    ref,
                    f"{len(chunks)} chunks share this artifact while "
                    "enrichment.max_media_chunk_tokens is 0 (atomic mode).",
                )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "media_atomicity_rate",
            rate(atomic, len(index.by_artifact_ref)) if atomic_expected else None,
            detail="one chunk per artifact_ref"
            if atomic_expected
            else "not applicable (row-group splitting enabled)",
        )
    )

    # ── Citation metadata ─────────────────────────────────────────────
    with_page = sum(1 for c in media if c.source_page is not None)
    with_bbox = sum(1 for c in media if _bbox_sane(c.bbox))
    with_model = sum(1 for c in media if c.enrichment_model)
    with_heading = sum(1 for c in media if c.section_heading.strip())

    report.add_metric(
        build_metric(STAGE2_INTEGRITY, "media_source_page_rate", rate(with_page, len(media)))
    )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "media_bbox_rate",
            rate(with_bbox, len(media)),
            detail="bbox present and geometrically sane",
        )
    )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "media_enrichment_model_rate",
            rate(with_model, len(media)),
            detail="enrichment_model is None when the VLM call degraded",
        )
    )
    for chunk in media:
        if not chunk.enrichment_model:
            report.add_finding(
                "error",
                "ENRICHMENT_DEGRADED",
                chunk.chunk_id,
                f"{chunk.document}: enrichment_model is null, meaning the VLM call failed "
                "and this chunk's text did not come from a transcription.",
            )
        if chunk.bbox is not None and not _bbox_sane(chunk.bbox):
            report.add_finding(
                "warn",
                "BBOX_DEGENERATE",
                chunk.chunk_id,
                f"{chunk.document}: bbox {chunk.bbox} has zero or negative area.",
            )
    report.meta["media_with_section_heading"] = f"{with_heading}/{len(media)}"

    # ── Parent linkage ────────────────────────────────────────────────
    linked = sum(1 for c in media if c.parent_chunk_id)
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "media_parent_linkage_rate",
            rate(linked, len(media)),
            detail="media chunks with a parent_chunk_id",
        )
    )
    if media and linked == 0:
        report.add_finding(
            "warn",
            "MEDIA_HAS_NO_PARENT",
            "corpus",
            f"None of the {len(media)} media chunks carry a parent_chunk_id, so step 5 "
            "parent expansion in RetrievalPipeline is a no-op for every figure: a "
            "retrieved chart arrives with no surrounding prose while text chunks get "
            "their full parent section. Not gated — record the decision or implement "
            "the linkage in HierarchicalChunker._build_media_chunks.",
        )

    # ── clause_reference plausibility ─────────────────────────────────
    plausible = 0
    implausible_rows: list[list[object]] = []
    for chunk in media:
        ref = chunk.clause_reference.strip()
        if not ref or _IMPLAUSIBLE_CLAUSE.match(ref) or _FALLBACK_CLAUSE.match(ref):
            implausible_rows.append([chunk.document, chunk.modality, ref or "(empty)", chunk.chunk_id[:12]])
        else:
            plausible += 1
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "clause_reference_plausibility_rate",
            rate(plausible, len(media)),
            detail="media clause_reference derived from caption text, so chart values leak in",
        )
    )
    if implausible_rows:
        report.tables.append(
            table(
                "Implausible media clause references",
                ["Document", "Modality", "clause_reference", "chunk_id"],
                implausible_rows,
                note="These are derived from caption text by "
                "`HierarchicalChunker._extract_clause_ref`, so a transcribed chart value "
                "(e.g. a utilisation percentage) can end up as the citation key. Use "
                "chunk_id, never clause_reference, as the identity key when scoring.",
            )
        )


def _bbox_sane(bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None or len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    # Docling's vertical axis origin varies by version, so only the magnitude of
    # the height is asserted, not its sign.
    return abs(x1 - x0) > 0 and abs(y1 - y0) > 0


def check_text_chunks(index: ChunkIndex, report: StageReport) -> None:
    """Structural health of the text side: orphans, duplicates, empties, tables."""
    children = [c for c in index.children if not c.is_media]
    parent_ids = {c.chunk_id for c in index.parents}

    orphans = [c for c in children if c.parent_chunk_id and c.parent_chunk_id not in parent_ids]
    for chunk in orphans:
        report.add_finding(
            "warn",
            "TEXT_ORPHAN",
            chunk.chunk_id,
            f"{chunk.document}: parent_chunk_id {chunk.parent_chunk_id} is not in the index, "
            "so parent expansion returns nothing for this chunk.",
        )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "text_orphan_rate",
            rate(len(orphans), len(children)),
            detail=f"{len(orphans)}/{len(children)} text children with a dangling parent",
        )
    )

    empty = [c for c in index.chunks if not c.text.strip()]
    for chunk in empty:
        report.add_finding("error", "EMPTY_CHUNK", chunk.chunk_id, f"{chunk.document}: empty text.")
    report.add_metric(
        build_metric(STAGE2_INTEGRITY, "empty_chunk_count", float(len(empty)), unit="count")
    )

    for chunk_id in index.duplicate_ids:
        report.add_finding("error", "DUPLICATE_CHUNK_ID", chunk_id, "Same chunk_id stored twice.")
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "duplicate_chunk_id_count",
            float(len(index.duplicate_ids)),
            unit="count",
        )
    )

    # High-confidence tables never become media chunks — they stay as markdown in
    # raw_markdown and go through RecursiveCharacterTextSplitter, which can cut a
    # table in half.
    fragmented = [c for c in children if _is_fragmented_table(c.text)]
    for chunk in fragmented:
        report.add_finding(
            "error",
            "TABLE_FRAGMENTED",
            chunk.chunk_id,
            f"{chunk.document} clause={chunk.clause_reference}: chunk contains markdown table "
            "rows with no header separator, i.e. a table split across chunk boundaries. "
            "Rows retrieved without their header lose the meaning of every column.",
        )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY, "fragmented_table_count", float(len(fragmented)), unit="count"
        )
    )

    tiny_children = [c for c in children if 0 < len(c.text) < _MIN_CHILD_CHARS]
    tiny_parents = [c for c in index.parents if 0 < len(c.text) < _MIN_PARENT_CHARS]
    for chunk in tiny_children:
        report.add_finding(
            "info",
            "TINY_CHUNK",
            chunk.chunk_id,
            f"{chunk.document}: {len(chunk.text)} chars (below {_MIN_CHILD_CHARS}).",
        )
    report.meta["tiny_children"] = len(tiny_children)
    report.meta["tiny_parents"] = len(tiny_parents)


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)


def _is_fragmented_table(text: str) -> bool:
    """True when the chunk holds table rows but not the header that defines them."""
    rows = _TABLE_ROW.findall(text)
    if len(rows) < 2:
        return False
    return not _TABLE_SEPARATOR.search(text)


def check_captions_shallow(index: ChunkIndex, report: StageReport) -> None:
    """Caption checks that need no ground truth: empties, illegibles, truncation."""
    media = index.media
    empty = [c for c in media if not c.text.strip()]
    illegible = [c for c in media if "[illegible]" in c.text.lower()]
    truncated = [c for c in media if looks_truncated(c.text)]

    for chunk in truncated:
        report.add_finding(
            "warn",
            "CAPTION_TRUNCATED",
            chunk.chunk_id,
            f"{chunk.document}: caption ends on a dangling separator or unclosed bracket, "
            "which is how a VLM transcription that hit max_tokens looks. Raise "
            "enrichment.max_tokens and re-ingest this document.",
        )
    report.add_metric(
        build_metric(
            STAGE2_INTEGRITY,
            "caption_truncation_count",
            float(len(truncated)),
            unit="count",
            detail=f"{len(truncated)}/{len(media)} captions look truncated",
        )
    )
    report.meta["captions_with_illegible_marker"] = f"{len(illegible)}/{len(media)}"
    report.meta["empty_captions"] = f"{len(empty)}/{len(media)}"


def summarise(index: ChunkIndex, report: StageReport) -> None:
    """Corpus shape table — the orientation a reviewer needs before the findings."""
    rows: list[list[object]] = []
    for document in index.documents:
        chunks = index.by_document[document]
        rows.append(
            [
                document,
                sum(1 for c in chunks if c.is_parent),
                sum(1 for c in chunks if not c.is_parent and not c.is_media),
                sum(1 for c in chunks if c.modality == "figure"),
                sum(1 for c in chunks if c.modality == "table"),
                sum(1 for c in chunks if c.is_media and not c.enrichment_model),
            ]
        )
    report.tables.append(
        table(
            "Corpus composition",
            ["Document", "Parents", "Text children", "Figures", "Tables", "Degraded media"],
            rows,
            note="'Degraded media' counts media chunks whose enrichment_model is null — "
            "their text did not come from a successful VLM transcription.",
        )
    )
    report.meta.update(
        {
            "collection": index.collection,
            "chunks_total": len(index.chunks),
            "media_chunks": len(index.media),
            "documents": len(index.documents),
        }
    )
