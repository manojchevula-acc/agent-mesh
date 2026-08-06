"""HF dual-encoder provider — SigLIP / SigLIP-2 / CLIP via transformers.AutoModel.

The default provider: covers any model exposing ``get_text_features`` and
``get_image_features``, which is most of the CLIP family.
"""

import asyncio
from functools import partial
from typing import Any, Sequence

from ...config.multimodal import MultimodalEmbeddingConfig
from ...utils.hashing import make_space_id
from ...utils.logging import get_logger
from ..base import EmbeddingOutput, EmbeddingSpace
from .base import BaseMultimodalEmbedder, ImageInput, to_pil
from .loader import (
    configure_torch_cpu,
    hub_kwargs,
    l2_normalize,
    maybe_quantize,
    resolve_dtype,
)
from .registry import ModelSpec, register_provider

logger = get_logger(__name__)


@register_provider("hf_dual_encoder")
class HFDualEncoderEmbedder(BaseMultimodalEmbedder):
    """Lazy-loading, thread-pool-dispatched dual encoder.

    Follows the concurrency pattern already used by BGEM3Embedder: the model is
    CPU-bound, so every forward pass runs in the default executor via
    ``loop.run_in_executor`` and never blocks the event loop.
    """

    def __init__(self, config: MultimodalEmbeddingConfig, spec: ModelSpec) -> None:
        self._config = config
        self._spec = spec
        self._model: Any = None
        self._processor: Any = None
        self._space: EmbeddingSpace | None = None
        logger.info(
            "Initialising HF dual encoder",
            model=spec.hf_id,
            dim_hint=spec.dim,
            device=config.device,
        )

    # ── Loading ──────────────────────────────────────────────────────
    def load(self) -> None:
        if self._model is not None:
            return
        from transformers import AutoModel, AutoProcessor

        configure_torch_cpu(self._config)
        kwargs = hub_kwargs(self._config)

        self._processor = AutoProcessor.from_pretrained(self._spec.hf_id, **kwargs)
        model = AutoModel.from_pretrained(
            self._spec.hf_id, dtype=resolve_dtype(self._config), **kwargs
        )
        model.eval()
        model.to(self._config.device)
        self._model = maybe_quantize(model, self._config)

        dim = self._probe_dim()
        self._assert_dim(dim)
        self._space = EmbeddingSpace(
            space_id=make_space_id(
                self._spec.provider,
                self._spec.hf_id,
                self._config.revision,
                dim,
                self._config.normalize,
                self._config.distance_metric,
            ),
            provider=self._spec.provider,
            model_name=self._spec.hf_id,
            revision=self._config.revision,
            dim=dim,
            metric=self._config.distance_metric,
            normalized=self._config.normalize,
            modalities=frozenset({"text", "image"}),
        )
        logger.info(
            "Multimodal model loaded",
            model=self._spec.hf_id,
            dim=dim,
            space_id=self._space.space_id,
        )

    def _probe_dim(self) -> int:
        """Discover the true output dim by encoding one token.

        Never trust the config: a wrong dim silently produces an unusable index.
        """
        import torch

        inputs = self._processor(
            text=["probe"],
            padding="max_length",
            truncation=True,
            max_length=self._effective_max_len(),
            return_tensors="pt",
        ).to(self._config.device)
        with torch.inference_mode():
            feats = self._model.get_text_features(**inputs)
        dim = int(feats.shape[-1])
        return min(dim, self._config.truncate_dim) if self._config.truncate_dim else dim

    def _assert_dim(self, probed: int) -> None:
        declared = self._config.embedding_dim or self._spec.dim
        if declared and declared != probed and not self._config.truncate_dim:
            raise ValueError(
                f"Embedding dim mismatch for {self._spec.hf_id}: config/registry "
                f"declares {declared}, model produces {probed}. Fix the config or "
                "the registry — a mismatch corrupts the index."
            )

    def _effective_max_len(self) -> int:
        configured = self._config.max_text_length or self._spec.max_text_length
        if configured:
            return configured
        tokenizer = getattr(self._processor, "tokenizer", None)
        model_max = getattr(tokenizer, "model_max_length", 64) if tokenizer else 64
        # Some tokenizers report a sentinel of 1e30 when unset.
        return 64 if model_max > 100_000 else int(model_max)

    # ── Encoding (sync, runs in an executor) ─────────────────────────
    def _sync_embed_texts(self, texts: list[str]) -> list[list[float]]:
        import torch

        self.load()
        out: list[list[float]] = []
        batch_size = self._config.text_batch_size
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            # SigLIP REQUIRES padding='max_length' — it is trained with
            # fixed-length text sequences and dynamic padding degrades quality.
            inputs = self._processor(
                text=batch,
                padding="max_length",
                truncation=True,
                max_length=self._effective_max_len(),
                return_tensors="pt",
            ).to(self._config.device)
            with torch.inference_mode():
                feats = self._model.get_text_features(**inputs)
            out.extend(self._postprocess(feats))
        return out

    def _sync_embed_images(self, images: Sequence[ImageInput]) -> list[list[float]]:
        import torch

        self.load()
        out: list[list[float]] = []
        batch_size = self._config.image_batch_size
        pil = [to_pil(im) for im in images]
        for i in range(0, len(pil), batch_size):
            inputs = self._processor(images=pil[i : i + batch_size], return_tensors="pt").to(
                self._config.device
            )
            with torch.inference_mode():
                feats = self._model.get_image_features(**inputs)
            out.extend(self._postprocess(feats))
        return out

    def _postprocess(self, feats: Any) -> list[list[float]]:
        if self._config.truncate_dim:  # Matryoshka
            feats = feats[..., : self._config.truncate_dim]
        if self._config.normalize:  # cosine == dot product
            feats = l2_normalize(feats)
        return feats.float().cpu().tolist()

    # ── Async surface ────────────────────────────────────────────────
    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        if not texts:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        dense = await loop.run_in_executor(None, partial(self._sync_embed_texts, texts))
        return EmbeddingOutput(dense_vectors=dense)

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    async def embed_images(self, images: Sequence[ImageInput]) -> EmbeddingOutput:
        if not images:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        dense = await loop.run_in_executor(
            None, partial(self._sync_embed_images, list(images))
        )
        return EmbeddingOutput(dense_vectors=dense)

    @property
    def space(self) -> EmbeddingSpace:
        if self._space is None:
            self.load()
        assert self._space is not None
        return self._space

    async def health_check(self) -> bool:
        try:
            out = await self.embed_query("health")
            return len(out.dense_vectors[0]) == self.space.dim
        except Exception as exc:  # noqa: BLE001
            logger.error("Multimodal embedder unhealthy", error=str(exc))
            return False
