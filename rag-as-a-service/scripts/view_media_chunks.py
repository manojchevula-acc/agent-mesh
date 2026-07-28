"""Inspect multimodal (image-as-text) chunks stored in Qdrant.

Lists every FIGURE / TABLE / PAGE_IMAGE chunk with its full VLM caption, the
artifact it points at, and which model produced it — so you can eyeball whether
enrichment produced real transcriptions or silently degraded. Cross-checks each
artifact_ref against the local artifact store on disk.

Usage:
    python scripts/view_media_chunks.py
    python scripts/view_media_chunks.py --modality figure
    python scripts/view_media_chunks.py --full-text        # don't truncate captions
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")

from qdrant_client import AsyncQdrantClient  # noqa: E402

from gernas_rag.config.settings import get_settings  # noqa: E402

_MEDIA_MODALITIES = {"figure", "table", "page_image"}


async def _scroll_all(client: AsyncQdrantClient, collection: str) -> list[dict]:
    """Paginate through the full collection — the 200-point sample other view
    scripts use can silently miss media chunks once a corpus has many documents.
    """
    points: list[dict] = []
    offset = None
    while True:
        batch, offset = await client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(p.payload or {} for p in batch)
        if offset is None:
            break
    return points


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modality", choices=sorted(_MEDIA_MODALITIES), default=None)
    parser.add_argument("--full-text", action="store_true", help="Don't truncate caption text")
    args = parser.parse_args()

    settings = get_settings()
    vcfg = settings.vectordb
    if vcfg.qdrant_path:
        client = AsyncQdrantClient(path=vcfg.qdrant_path)
    else:
        client = AsyncQdrantClient(url=vcfg.qdrant_url, api_key=vcfg.qdrant_api_key)

    all_payloads = await _scroll_all(client, vcfg.collection_name)
    media = [p for p in all_payloads if p.get("modality") in _MEDIA_MODALITIES]
    if args.modality:
        media = [p for p in media if p.get("modality") == args.modality]

    total_text = len(all_payloads) - len(
        [p for p in all_payloads if p.get("modality") in _MEDIA_MODALITIES]
    )
    print(f"Total chunks: {len(all_payloads)}  ·  text: {total_text}  ·  media: "
          f"{len([p for p in all_payloads if p.get('modality') in _MEDIA_MODALITIES])}")

    if not media:
        print("\nNo media chunks found. Either enrichment.enabled=false during ingestion, "
              "or the ingested documents had no figures/low-confidence tables.")
        return

    by_modality: dict[str, int] = {}
    for p in media:
        by_modality[p["modality"]] = by_modality.get(p["modality"], 0) + 1
    print("By modality:", ", ".join(f"{k}={v}" for k, v in sorted(by_modality.items())))

    artifact_root = Path(settings.artifact_store.local_path)
    warnings: list[str] = []

    print("\n=== Media chunks ===")
    for i, p in enumerate(sorted(media, key=lambda x: (x.get("document_name", ""), x.get("source_page") or 0)), 1):
        doc = p.get("document_name", "")
        modality = p.get("modality", "")
        page = p.get("source_page")
        heading = p.get("section_heading", "")
        model = p.get("enrichment_model")
        ref = p.get("artifact_ref", "")
        text = p.get("text", "")

        artifact_ok = False
        if ref:
            digest, _, ext = ref.removeprefix("sha256:").partition(".")
            artifact_ok = (artifact_root / f"{digest}.{ext}").exists()

        print(f"\n[{i}] {doc}  ·  p.{page}  ·  [{modality.upper()}]  ·  §{heading or '-'}")
        print(f"    artifact_ref     : {ref or '(none)'}  {'✓ file on disk' if artifact_ok else '✗ FILE MISSING' if ref else ''}")
        print(f"    enrichment_model : {model or '(none — VLM degraded, using fallback text)'}")
        print(f"    caption chars    : {len(text)}")
        shown = text if args.full_text else (text[:400] + ("…" if len(text) > 400 else ""))
        print(f"    caption          :\n      {shown}")

        if not model:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — enrichment_model is None: VLM call likely failed or was skipped; caption is Docling's raw text, not a real transcription.")
        if ref and not artifact_ok:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — artifact_ref points at a file that doesn't exist under {artifact_root}: hydration will fail-soft to text-only for this chunk.")
        if len(text.strip()) < 20:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — caption is suspiciously short ({len(text)} chars): check the source image manually.")

    print("\n=== Warnings ===")
    if warnings:
        for w in warnings:
            print(f"  WARN  {w}")
    else:
        print("  None — every media chunk has a model attribution and a resolvable artifact.")

    print(f"\nOpen the artifact image directly to compare against the caption above, e.g.:")
    print(f"  {artifact_root / '<digest>.png'}")


if __name__ == "__main__":
    asyncio.run(main())
