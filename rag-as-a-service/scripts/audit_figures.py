"""Phase 0 gate on the multimodal encoder investment.

Counts, per document: images extracted, images surviving the filters, and what
fraction carry an extractable caption. The caption rate decides how much the
image embedding buys over caption-only stubs:

  >70% captioned  -> stubs capture most of the value; Phase 3 is optional
  <40% captioned  -> Phase 3 is the ONLY way those figures are retrievable

Usage:
    python scripts/audit_figures.py --path ./docs
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.images.factory import get_image_extractor  # noqa: E402
from gernas_rag.images.filters import ImageFilter  # noqa: E402


async def audit_file(path: Path, config, chunking) -> dict:
    extractor = get_image_extractor(config, chunking, path)
    image_filter = ImageFilter(config)
    raws = await extractor.extract_images(path)

    stats: Counter = Counter(extracted=len(raws))
    kept = 0
    captioned = 0
    for raw in raws:
        verdict = image_filter.evaluate(raw)
        if not verdict.keep:
            stats[f"rejected_{verdict.reason}"] += 1
            continue
        kept += 1
        if raw.caption.strip():
            captioned += 1

    return {
        "document": path.name,
        "extracted": len(raws),
        "kept": kept,
        "captioned": captioned,
        "caption_rate": (captioned / kept) if kept else 0.0,
        "rejections": {k: v for k, v in stats.items() if k.startswith("rejected_")},
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="./docs")
    args = parser.parse_args()

    settings = get_settings()
    config = settings.multimodal.extraction
    root = Path(args.path)
    files = (
        [root]
        if root.is_file()
        else [f for ext in settings.ingestion.supported_extensions for f in root.glob(f"**/*{ext}")]
    )

    rows = []
    for f in files:
        try:
            rows.append(await audit_file(f, config, settings.chunking))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {f.name}: {exc}")

    print(f"\n{'Document':<52} {'Extracted':>9} {'Kept':>6} {'Capt':>6} {'Rate':>7}")
    print("-" * 84)
    total_kept = total_capt = 0
    for r in rows:
        total_kept += r["kept"]
        total_capt += r["captioned"]
        print(
            f"{r['document'][:52]:<52} {r['extracted']:>9} {r['kept']:>6} "
            f"{r['captioned']:>6} {r['caption_rate']:>6.0%}"
        )

    overall = (total_capt / total_kept) if total_kept else 0.0
    print("-" * 84)
    print(f"{'TOTAL':<52} {'':>9} {total_kept:>6} {total_capt:>6} {overall:>6.0%}")

    print("\nRejection reasons:")
    merged: Counter = Counter()
    for r in rows:
        merged.update(r["rejections"])
    for reason, count in merged.most_common():
        print(f"  {reason:<32} {count}")

    backend = type(get_image_extractor(config, settings.chunking)).__name__
    print(f"\nBackend: {backend}")
    if "PyMuPDF" in backend:
        print(
            "  WARNING: PyMuPDF has no caption linkage, so the caption rate above is\n"
            "  structurally 0% and says nothing about the corpus. Re-run with\n"
            "  RAG__MULTIMODAL__EXTRACTION__BACKEND=docling for a meaningful rate.\n"
            "  PyMuPDF also misses vector-drawn charts and tables entirely."
        )

    print("\nVerdict:")
    if "PyMuPDF" in backend:
        print("  Caption rate is not measurable with this backend — see the warning above.")
        print(f"  Figure count ({total_kept} across {len(rows)} documents) is still valid.")
    elif total_kept == 0:
        print("  No figures survive filtering. Phase 3 buys nothing on this corpus;")
        print("  the table work (D8) is where the value is.")
    elif overall > 0.70:
        print("  >70% captioned: caption stubs capture most of the retrieval value.")
        print("  Phase 3 still helps generation (the LLM reads the chart, not the caption).")
    elif overall < 0.40:
        print("  <40% captioned: Phase 3 is the ONLY route to those figures. Clear go.")
    else:
        print("  Mixed. Phase 3 is worthwhile; measure Recall@1 before tuning further.")


if __name__ == "__main__":
    asyncio.run(main())
