"""Stage 2a — Index & artifact integrity.

No ground truth file, because none is needed: these are STRUCTURAL invariants.
Either every image vector resolves to a file on disk or it does not. Either a
table chunk kept its header or it did not. A violation is a bug by definition,
which makes this the cheapest stage to run and the first one to check when
something downstream looks strange.

What it cross-checks:
  Qdrant text collection  <->  Qdrant image collection  <->  LocalAssetStore

Ports the intent of scripts/verify_chunks.py, audit_tables.py and
verify_tables.py into one report.
"""

import asyncio
import sys
from collections import Counter

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.images.store import get_asset_store  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

from ..core import report  # noqa: E402

_SCROLL_LIMIT = 100_000


async def _scroll(client, collection: str) -> list:
    points, _ = await client.scroll(
        collection_name=collection, limit=_SCROLL_LIMIT, with_payload=True, with_vectors=False
    )
    return points


def _table_atomicity(chunks: list[dict]) -> tuple[int, int, list[str]]:
    """A table chunk must carry its header row.

    Row-split tables repeat the header in each part by design (see the
    table-atomic handler), so a part WITHOUT a header means the split lost it
    and the LLM receives unlabelled numbers — which is how a 260 bps floor gets
    reported under the wrong rating.
    """
    tables = [c for c in chunks if c.get("content_type") == "table"]
    broken = []
    for chunk in tables:
        text = chunk.get("text") or ""
        # A markdown table header is a line of pipes followed by a separator
        # row of dashes. Absence of the separator means no header survived.
        has_header = "|" in text and any(
            set(line.strip()) <= set("|-: ") and "-" in line
            for line in text.splitlines()[:4]
        )
        if not has_header:
            broken.append(f"{chunk.get('document_name')} :: {(text[:60] or '').strip()}")
    return len(tables) - len(broken), len(tables), broken


def run() -> report.StageResult:
    settings = get_settings()
    result = report.StageResult(
        stage="stage2a",
        title="Stage 2a — Index & Artifact Integrity",
        context={"vectordb": settings.vectordb.provider},
    )

    vectordb = get_vectordb(settings.vectordb)
    client = vectordb._client  # noqa: SLF001 - eval tooling reads the raw client deliberately

    text_collection = settings.vectordb.collection_name
    image_collection = settings.multimodal.image_collection_name
    if not image_collection and settings.multimodal.enabled:
        from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder

        space = get_multimodal_embedder(settings.multimodal.embedding).space
        image_collection = space.collection_name(settings.multimodal.image_collection_base)

    async def _load():
        texts = await _scroll(client, text_collection)
        images = (
            await _scroll(client, image_collection)
            if settings.multimodal.enabled and image_collection
            else []
        )
        return texts, images

    text_points, image_points = asyncio.run(_load())
    chunks = [p.payload or {} for p in text_points]
    assets = [p.payload or {} for p in image_points]

    result.context["text_collection"] = f"{text_collection} ({len(chunks)} chunks)"
    result.context["image_collection"] = f"{image_collection} ({len(assets)} vectors)"

    # ── 1. Every indexed image vector must resolve to bytes on disk ──
    store = get_asset_store(settings.multimodal.storage)
    indexed_ids = {a.get("id") for a in assets if a.get("id")}
    resolvable = {i for i in indexed_ids if store.exists(i)}
    missing = sorted(indexed_ids - resolvable)

    result.add(
        "asset_resolvable_rate",
        (len(resolvable) / len(indexed_ids)) if indexed_ids else report.NA,
        f"{len(resolvable)}/{len(indexed_ids)} vectors have bytes on disk",
    )
    for asset_id in missing[:10]:
        owner = next((a for a in assets if a.get("id") == asset_id), {})
        result.rows.append(
            {
                "issue": "vector without asset",
                "detail": f"{owner.get('document_name')} p{owner.get('page_number')} -> {asset_id}",
            }
        )

    # ── 2. Image-stub coverage: captions must be lexically searchable ──
    # Every indexed image should have a stub chunk in the TEXT collection, or
    # BM25 can never surface it by caption — only the dense image tower can.
    stub_asset_ids = {
        c.get("asset_id")
        for c in chunks
        if c.get("content_type") == "image_stub" and c.get("asset_id")
    }
    covered = indexed_ids & stub_asset_ids
    result.add(
        "stub_coverage",
        (len(covered) / len(indexed_ids)) if indexed_ids else report.NA,
        f"{len(covered)}/{len(indexed_ids)} images have a caption stub chunk",
    )

    # ── 3. Table atomicity ───────────────────────────────────────────
    intact, total_tables, broken = _table_atomicity(chunks)
    result.add(
        "table_atomicity",
        (intact / total_tables) if total_tables else report.NA,
        f"{intact}/{total_tables} table chunks retain a header row",
    )
    for item in broken[:10]:
        result.rows.append({"issue": "table chunk without header", "detail": item})

    # ── 4. Orphaned assets (watch-metric) ────────────────────────────
    # Bytes on disk that no vector points at. Not corruption — usually a
    # re-ingest that changed boundaries — but it is dead storage, and a large
    # number means reconciliation is not running.
    on_disk = {
        path.stem
        for path in __import__("pathlib").Path(settings.multimodal.storage.root).rglob("*")
        if path.is_file() and not path.stem.endswith("_thumb")
    }
    orphans = on_disk - indexed_ids
    result.add(
        "orphan_rate",
        (len(orphans) / len(on_disk)) if on_disk else report.NA,
        f"{len(orphans)}/{len(on_disk)} stored assets have no vector",
    )

    if not settings.multimodal.enabled:
        result.notes.append("multimodal.enabled is false — image metrics report n/a.")
    result.notes.append(
        "Structural invariants only: no ground truth needed, so any failure "
        "here is a defect rather than a tuning question."
    )
    return result
