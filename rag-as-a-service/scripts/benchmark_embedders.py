"""Measure multimodal encoder cost ON YOUR HARDWARE.

The performance tables in design/multimodal-rag-poc.md are engineering estimates.
Run this and replace them with measured numbers before making any decision that
depends on them.

Usage:
    python scripts/benchmark_embedders.py --models siglip2-base,openclip-b32
    python scripts/benchmark_embedders.py --models siglip2-base --int8
"""

import argparse
import asyncio
import gc
import os
import sys
import time

sys.path.insert(0, "src")

from gernas_rag.config.multimodal import MultimodalEmbeddingConfig  # noqa: E402
from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder  # noqa: E402


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        return float("nan")


def _synthetic_images(count: int, side: int = 512):
    from PIL import Image, ImageDraw

    images = []
    for i in range(count):
        im = Image.new("RGB", (side, side), (250, 250, 250))
        draw = ImageDraw.Draw(im)
        for bar in range(6):
            height = 40 + ((i * 37 + bar * 53) % 300)
            x = 40 + bar * 70
            draw.rectangle([x, side - 60 - height, x + 45, side - 60], fill=(60, 100, 180))
        draw.line([40, side - 60, side - 40, side - 60], fill=(0, 0, 0), width=3)
        images.append(im)
    return images


async def benchmark(alias: str, int8: bool, n_images: int, n_texts: int) -> dict:
    config = MultimodalEmbeddingConfig(model_name=alias, quantize_dynamic_int8=int8)
    baseline_rss = _rss_mb()

    embedder = get_multimodal_embedder(config)
    t0 = time.perf_counter()
    embedder.load()
    load_s = time.perf_counter() - t0
    loaded_rss = _rss_mb()

    texts = [f"pricing floor for a BBB-rated {i}-year AED facility" for i in range(n_texts)]
    images = _synthetic_images(n_images)

    await embedder.embed_query("warmup")  # exclude first-call overhead
    await embedder.embed_images(images[:1])

    t0 = time.perf_counter()
    await embedder.embed_documents(texts)
    text_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    await embedder.embed_images(images)
    image_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    await embedder.embed_query("single query latency")
    query_ms = (time.perf_counter() - t0) * 1000

    result = {
        "alias": alias,
        "model": embedder.space.model_name,
        "dim": embedder.space.dim,
        "int8": int8,
        "load_s": round(load_s, 2),
        "rss_mb": round(loaded_rss - baseline_rss, 1),
        "texts_per_s": round(n_texts / text_s, 1),
        "images_per_s": round(n_images / image_s, 2),
        "query_ms": round(query_ms, 1),
    }
    del embedder
    gc.collect()
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="siglip2-base")
    parser.add_argument("--images", type=int, default=16)
    parser.add_argument("--texts", type=int, default=64)
    parser.add_argument("--int8", action="store_true")
    args = parser.parse_args()

    rows = []
    for alias in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"Benchmarking {alias} ...", flush=True)
        try:
            rows.append(await benchmark(alias, args.int8, args.images, args.texts))
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}")

    if not rows:
        return

    print("\n| Model | Dim | int8 | Load (s) | RSS (MB) | Texts/s | Images/s | Query (ms) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| `{r['model']}` | {r['dim']} | {r['int8']} | {r['load_s']} | "
            f"{r['rss_mb']} | {r['texts_per_s']} | {r['images_per_s']} | {r['query_ms']} |"
        )
    print(
        "\nPaste this into design/multimodal-rag-poc.md section 14, replacing the "
        "estimate tables."
    )
    print(
        "NOTE: if you ran with --int8, the score floor MUST be recalibrated "
        "(scripts/eval_multimodal.py --sweep-floor) — quantisation shifts the "
        "similarity distribution."
    )


if __name__ == "__main__":
    asyncio.run(main())
