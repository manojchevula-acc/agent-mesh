"""Torch/CPU runtime setup — shared by every provider."""

import os
from typing import Any

from ...config.multimodal import MultimodalEmbeddingConfig
from ...utils.logging import get_logger

logger = get_logger(__name__)

_DTYPES = ("float32", "bfloat16", "float16")


def configure_torch_cpu(config: MultimodalEmbeddingConfig) -> None:
    """Pin thread counts BEFORE the first forward pass.

    Oversubscription is the most common cause of bad CPU embedding latency:
    torch's intra-op pool fights uvicorn workers and the ThreadPoolExecutor we
    dispatch into. Half the cores, capped at 8, is a good default.
    """
    import torch

    if config.device != "cpu":
        return
    threads = config.torch_num_threads or min(8, max(1, (os.cpu_count() or 4) // 2))
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        # Only settable before any parallel work has started; harmless if late.
        pass
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logger.info("Torch CPU configured", intra_op_threads=threads)


def resolve_dtype(config: MultimodalEmbeddingConfig) -> Any:
    import torch

    name = config.dtype if config.dtype in _DTYPES else "float32"
    return getattr(torch, name)


def maybe_quantize(model: Any, config: MultimodalEmbeddingConfig) -> Any:
    """Dynamic int8 quantisation of Linear layers.

    Typically 1.3-2x faster on CPU with a small quality cost. MUST be paired with
    a re-run of the golden alignment test: quantisation shifts the similarity
    distribution, so the score floor needs recalibrating.
    """
    if not config.quantize_dynamic_int8 or config.device != "cpu":
        return model
    try:
        import torch

        quantized = torch.ao.quantization.quantize_dynamic(
            model, {torch.nn.Linear}, dtype=torch.qint8
        )
        logger.info("Applied dynamic int8 quantisation")
        return quantized
    except Exception as exc:  # noqa: BLE001 - an optimisation must never break loading
        logger.warning("Dynamic quantisation failed; using fp32", error=str(exc))
        return model


def l2_normalize(tensor: Any) -> Any:
    import torch.nn.functional as F

    return F.normalize(tensor, p=2, dim=-1)


def hub_kwargs(config: MultimodalEmbeddingConfig) -> dict[str, Any]:
    """Shared from_pretrained kwargs."""
    kwargs: dict[str, Any] = {
        "trust_remote_code": config.trust_remote_code,
        "local_files_only": config.local_files_only,
    }
    if config.revision:
        kwargs["revision"] = config.revision
    if config.cache_dir:
        kwargs["cache_dir"] = config.cache_dir
    return kwargs
