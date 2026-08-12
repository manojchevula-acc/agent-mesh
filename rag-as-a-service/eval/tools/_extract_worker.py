"""Extract one PDF's image crops and print the id bridge as JSON.

Runs as its own process:  python -m eval.tools._extract_worker <pdf>

It is a separate process on purpose. Docling image extraction SEGFAULTS on at
least one document in this corpus (CBUAE_Circular_2024_BSE_047_AI_Governance),
and a segfault cannot be caught with try/except — it takes the interpreter down
with it. Isolating each document means one bad PDF costs one document's
mapping instead of the entire run.

Output on stdout is a single JSON object, prefixed by a sentinel line so
Docling's logging on stdout/stderr cannot corrupt the parse.
"""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

SENTINEL = "---EVAL-WORKER-JSON---"


async def _extract(pdf: Path) -> list[dict]:
    from gernas_rag.config.settings import get_settings
    from gernas_rag.images.factory import get_image_extractor
    from gernas_rag.images.filters import ImageFilter
    from gernas_rag.images.preprocess import normalize

    settings = get_settings()
    extractor = get_image_extractor(settings.multimodal.extraction, settings.chunking, pdf)
    image_filter = ImageFilter(settings.multimodal.extraction)

    out = []
    for raw in await extractor.extract_images(pdf):
        # Mirror ingestion exactly: a crop the filter rejected never became an
        # asset, so it cannot be mapped. It is still REPORTED, with the reason,
        # so "the pipeline deliberately dropped this" is distinguishable from
        # "the hash did not match" — those need different follow-up.
        verdict = image_filter.evaluate(raw)
        if not verdict.keep:
            out.append(
                {
                    "their_ref": f"sha256:{hashlib.sha256(raw.data).hexdigest()}.png",
                    "asset_id": None,
                    "page": raw.page_number,
                    "role": getattr(raw.role, "value", str(raw.role)),
                    "rejected": verdict.reason,
                }
            )
            continue
        try:
            norm_bytes, _pil = normalize(raw.data, settings.multimodal.storage)
        except Exception:  # noqa: BLE001 - undecodable, same as ingestion
            continue
        out.append(
            {
                "their_ref": f"sha256:{hashlib.sha256(raw.data).hexdigest()}.png",
                "asset_id": hashlib.sha256(norm_bytes).hexdigest()[:32],
                "page": raw.page_number,
                "role": getattr(raw.role, "value", str(raw.role)),
                "rejected": None,
            }
        )
    return out


def main() -> int:
    pdf = Path(sys.argv[1])
    crops = asyncio.run(_extract(pdf))
    print(SENTINEL)
    print(json.dumps(crops))
    return 0


if __name__ == "__main__":
    sys.exit(main())
