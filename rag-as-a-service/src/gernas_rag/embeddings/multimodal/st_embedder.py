"""sentence-transformers provider — covers jina-clip-v2 and ST-packaged CLIPs.

ST's ``encode`` accepts a mixed list of strings and PIL images and routes each to
the right tower, so both methods reduce to one call.
"""

import asyncio
from functools import partial
from typing import Any, Sequence

from ...config.multimodal import MultimodalEmbeddingConfig
from ...utils.hashing import make_space_id
from ...utils.logging import get_logger
from ..base import EmbeddingOutput, EmbeddingSpace
from .base import BaseMultimodalEmbedder, ImageInput, to_pil
from .loader import configure_torch_cpu, resolve_dtype
from .registry import ModelSpec, register_provider

logger = get_logger(__name__)


@register_provider("st")
class STMultimodalEmbedder(BaseMultimodalEmbedder):
    def __init__(self, config: MultimodalEmbeddingConfig, spec: ModelSpec) -> None:
        self._config = config
        self._spec = spec
        self._model: Any = None
        self._space: EmbeddingSpace | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        configure_torch_cpu(self._config)
        kwargs: dict[str, Any] = {
            "device": self._config.device,
            "trust_remote_code": self._config.trust_remote_code,
            "model_kwargs": {"torch_dtype": resolve_dtype(self._config)},
        }
        if self._config.truncate_dim:  # Matryoshka
            kwargs["truncate_dim"] = self._config.truncate_dim
        if self._config.revision:
            kwargs["revision"] = self._config.revision
        if self._config.cache_dir:
            kwargs["cache_folder"] = self._config.cache_dir
        if self._config.local_files_only:
            kwargs["local_files_only"] = True

        self._model = SentenceTransformer(self._spec.hf_id, **kwargs)

        dim = len(self._encode(["probe"])[0])
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
        logger.info("SentenceTransformer multimodal model loaded",
                    model=self._spec.hf_id, dim=dim)

    def _encode(self, items: list[Any]) -> list[list[float]]:
        return self._model.encode(
            items,
            batch_size=self._config.image_batch_size,
            normalize_embeddings=self._config.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()

    def _sync_embed(self, items: list[Any]) -> list[list[float]]:
        self.load()
        return self._encode(items)

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        if not texts:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        return EmbeddingOutput(
            dense_vectors=await loop.run_in_executor(
                None, partial(self._sync_embed, list(texts))
            )
        )

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    async def embed_images(self, images: Sequence[ImageInput]) -> EmbeddingOutput:
        if not images:
            return EmbeddingOutput(dense_vectors=[])
        loop = asyncio.get_running_loop()
        pil = [to_pil(im) for im in images]
        return EmbeddingOutput(
            dense_vectors=await loop.run_in_executor(None, partial(self._sync_embed, pil))
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
            logger.error("ST multimodal embedder unhealthy", error=str(exc))
            return False
