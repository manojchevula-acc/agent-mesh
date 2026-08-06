"""OpenCLIP provider — LAION checkpoints via open_clip_torch.

Fastest CPU option; use on low-RAM machines or when ingestion throughput matters
more than text-rich-image quality.
"""

import asyncio
from functools import partial
from typing import Any, Sequence

from ...config.multimodal import MultimodalEmbeddingConfig
from ...utils.hashing import make_space_id
from ...utils.logging import get_logger
from ..base import EmbeddingOutput, EmbeddingSpace
from .base import BaseMultimodalEmbedder, ImageInput, to_pil
from .loader import configure_torch_cpu, l2_normalize, maybe_quantize
from .registry import ModelSpec, register_provider

logger = get_logger(__name__)


@register_provider("open_clip")
class OpenCLIPEmbedder(BaseMultimodalEmbedder):
    def __init__(self, config: MultimodalEmbeddingConfig, spec: ModelSpec) -> None:
        self._config = config
        self._spec = spec
        self._model: Any = None
        self._preprocess: Any = None
        self._tokenizer: Any = None
        self._space: EmbeddingSpace | None = None

    def _arch(self) -> tuple[str, str]:
        extra = self._spec.extra or {}
        return (
            extra.get("open_clip_arch", "ViT-B-32"),
            extra.get("open_clip_pretrained", "laion2b_s34b_b79k"),
        )

    def load(self) -> None:
        if self._model is not None:
            return
        import open_clip

        configure_torch_cpu(self._config)
        arch, pretrained = self._arch()
        model, _, preprocess = open_clip.create_model_and_transforms(
            arch, pretrained=pretrained, device=self._config.device
        )
        model.eval()
        self._model = maybe_quantize(model, self._config)
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(arch)

        dim = self._probe_dim()
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
        logger.info("OpenCLIP loaded", arch=arch, pretrained=pretrained, dim=dim)

    def _probe_dim(self) -> int:
        import torch

        with torch.inference_mode():
            feats = self._model.encode_text(
                self._tokenizer(["probe"]).to(self._config.device)
            )
        dim = int(feats.shape[-1])
        return min(dim, self._config.truncate_dim) if self._config.truncate_dim else dim

    def _postprocess(self, feats: Any) -> list[list[float]]:
        if self._config.truncate_dim:
            feats = feats[..., : self._config.truncate_dim]
        if self._config.normalize:
            feats = l2_normalize(feats)
        return feats.float().cpu().tolist()

    def _sync_embed_texts(self, texts: list[str]) -> list[list[float]]:
        import torch

        self.load()
        out: list[list[float]] = []
        bs = self._config.text_batch_size
        for i in range(0, len(texts), bs):
            tokens = self._tokenizer(texts[i : i + bs]).to(self._config.device)
            with torch.inference_mode():
                out.extend(self._postprocess(self._model.encode_text(tokens)))
        return out

    def _sync_embed_images(self, images: Sequence[ImageInput]) -> list[list[float]]:
        import torch

        self.load()
        out: list[list[float]] = []
        bs = self._config.image_batch_size
        tensors = [self._preprocess(to_pil(im)) for im in images]
        for i in range(0, len(tensors), bs):
            batch = torch.stack(tensors[i : i + bs]).to(self._config.device)
            with torch.inference_mode():
                out.extend(self._postprocess(self._model.encode_image(batch)))
        return out

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        if not texts:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        return EmbeddingOutput(
            dense_vectors=await loop.run_in_executor(
                None, partial(self._sync_embed_texts, texts)
            )
        )

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    async def embed_images(self, images: Sequence[ImageInput]) -> EmbeddingOutput:
        if not images:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        return EmbeddingOutput(
            dense_vectors=await loop.run_in_executor(
                None, partial(self._sync_embed_images, list(images))
            )
        )

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
            logger.error("OpenCLIP embedder unhealthy", error=str(exc))
            return False
