"""Inspect multimodal (image-as-text) chunks stored in Qdrant.

Lists every FIGURE / TABLE / PAGE_IMAGE chunk with its full VLM caption, the
artifact it points at, and which model produced it — so you can eyeball whether
enrichment produced real transcriptions or silently degraded. Cross-checks each
artifact_ref against the local artifact store on disk, and also flags the
reverse case: an image file that was extracted and stored but never became a
chunk (enrichment failed or produced an empty caption, so it was silently
fail-soft dropped) — otherwise that image has no trace anywhere in this report.

Usage:
    python scripts/view_media_chunks.py
    python scripts/view_media_chunks.py --modality figure
    python scripts/view_media_chunks.py --full-text        # don't truncate captions
    python scripts/view_media_chunks.py --report artifacts/media_chunks_report.md
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

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
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Also write the full (untruncated) per-image report to this Markdown file",
    )
    args = parser.parse_args()

    settings = get_settings()
    vcfg = settings.vectordb
    if vcfg.qdrant_path:
        client = AsyncQdrantClient(path=vcfg.qdrant_path)
    else:
        client = AsyncQdrantClient(url=vcfg.qdrant_url, api_key=vcfg.qdrant_api_key)

    all_payloads = await _scroll_all(client, vcfg.collection_name)
    all_media = [p for p in all_payloads if p.get("modality") in _MEDIA_MODALITIES]
    media = all_media
    if args.modality:
        media = [p for p in media if p.get("modality") == args.modality]

    total_text = len(all_payloads) - len(all_media)
    print(f"Total chunks: {len(all_payloads)}  ·  text: {total_text}  ·  media: {len(all_media)}")

    if not all_media:
        print("\nNo media chunks found. Either enrichment.enabled=false during ingestion, "
              "or the ingested documents had no figures/low-confidence tables.")
        return
    if not media:
        print(f"\nNo chunks match --modality {args.modality!r}.")
        return

    by_modality: dict[str, int] = {}
    for p in media:
        by_modality[p["modality"]] = by_modality.get(p["modality"], 0) + 1
    print("By modality:", ", ".join(f"{k}={v}" for k, v in sorted(by_modality.items())))

    artifact_root = Path(settings.artifact_store.local_path)
    warnings: list[str] = []
    # Full-detail lines for every image, always untruncated — this is what
    # --report persists, independent of --full-text (which only affects stdout).
    report_sections: list[str] = [
        "# Media chunk report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Total chunks: {len(all_payloads)}  ·  text: {total_text}  ·  media: {len(media)}",
        "",
    ]

    print("\n=== Media chunks ===")
    for i, p in enumerate(sorted(media, key=lambda x: (x.get("document_name", ""), x.get("source_page") or 0)), 1):
        doc = p.get("document_name", "")
        modality = p.get("modality", "")
        page = p.get("source_page")
        heading = p.get("section_heading", "")
        model = p.get("enrichment_model")
        ref = p.get("artifact_ref", "")
        bbox = p.get("bbox")
        text = p.get("text", "")

        artifact_ok = False
        if ref:
            digest, _, ext = ref.removeprefix("sha256:").partition(".")
            artifact_ok = (artifact_root / f"{digest}.{ext}").exists()

        print(f"\n[{i}] {doc}  ·  p.{page}  ·  [{modality.upper()}]  ·  §{heading or '-'}")
        print(f"    artifact_ref     : {ref or '(none)'}  {'[OK] file on disk' if artifact_ok else '[MISSING] file not found' if ref else ''}")
        print(f"    enrichment_model : {model or '(none — VLM degraded, using fallback text)'}")
        print(f"    bbox             : {bbox or '(none)'}")
        print(f"    caption chars    : {len(text)}")
        shown = text if args.full_text else (text[:400] + ("…" if len(text) > 400 else ""))
        print(f"    caption          :\n      {shown}")

        report_sections.append(
            f"## [{i}] image_id: `{ref or '(none)'}`\n\n"
            f"- document: {doc}  ·  page: {page}  ·  modality: {modality}\n"
            f"- section_heading: {heading or '(none)'}\n"
            f"- bbox: {bbox or '(none)'}\n"
            f"- enrichment_model: {model or '(none — VLM degraded)'}\n"
            f"- artifact file on disk: {'yes' if artifact_ok else 'NO' if ref else 'n/a'}\n"
            f"- caption length: {len(text)} chars\n\n"
            f"**Generated summary:**\n\n```\n{text}\n```\n"
        )

        if not model:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — enrichment_model is None: VLM call likely failed or was skipped; caption is Docling's raw text, not a real transcription.")
        if ref and not artifact_ok:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — artifact_ref points at a file that doesn't exist under {artifact_root}: hydration will fail-soft to text-only for this chunk.")
        if len(text.strip()) < 20:
            warnings.append(f"[{i}] {doc} p.{page} ({modality}) — caption is suspiciously short ({len(text)} chars): check the source image manually.")

    # Reverse check: an image extracted and stored to the artifact store but with
    # no chunk pointing at it (VLM caption failed/empty -> fail-soft dropped the
    # element in _build_media_chunks). Based on *all* media chunks in the index,
    # not the --modality-filtered subset, so filtering never produces false
    # positives here.
    referenced_digests = {
        (p.get("artifact_ref") or "").removeprefix("sha256:").split(".")[0]
        for p in all_media
        if p.get("artifact_ref")
    }
    orphaned_files: list[Path] = []
    if artifact_root.is_dir():
        image_files = [
            f for ext in ("*.png", "*.jpg", "*.jpeg") for f in artifact_root.glob(ext)
        ]
        orphaned_files = sorted(f for f in image_files if f.stem not in referenced_digests)

    print("\n=== Warnings ===")
    if warnings:
        for w in warnings:
            print(f"  WARN  {w}")
    else:
        print("  None — every media chunk has a model attribution and a resolvable artifact.")

    if orphaned_files:
        print("\n=== Orphaned artifacts (extracted, never chunked) ===")
        for f in orphaned_files:
            print(f"  ORPHAN  {f.name} — stored but has no chunk in the index; "
                  f"enrichment failed or returned an empty caption for this image "
                  f"and it was silently dropped. Open the file directly and check "
                  f"the source PDF for what's missing from retrieval.")

    print(f"\nOpen the artifact image directly to compare against the caption above, e.g.:")
    print(f"  {artifact_root / '<digest>.png'}")

    if args.report:
        report_sections.append("## Warnings\n")
        report_sections.append(
            "\n".join(f"- WARN {w}" for w in warnings) if warnings else "None."
        )
        report_sections.append("\n## Orphaned artifacts (extracted, never chunked)\n")
        if orphaned_files:
            report_sections.append(
                "\n".join(
                    f"- `{f.name}` — stored but has no chunk in the index; "
                    "enrichment failed or returned an empty caption and it was "
                    "silently dropped."
                    for f in orphaned_files
                )
            )
        else:
            report_sections.append("None — every stored artifact has a corresponding chunk.")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(report_sections), encoding="utf-8")
        print(f"\nFull report written to: {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
