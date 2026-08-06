"""Multimodal embedder contract — text and images in ONE shared space."""

from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Sequence, Union

from ..base import BaseEmbedder, EmbeddingSpace

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

# Anything we can turn into a PIL image.
ImageInput = Union[str, Path, bytes, "PILImage"]


class BaseMultimodalEmbedder(BaseEmbedder, ABC):
    """A dual encoder that maps text AND images into the same vector space.

    Inherits :class:`BaseEmbedder` so it is drop-in usable anywhere a text
    embedder is expected: ``embed_query`` uses the TEXT tower, ``embed_images``
    the VISION tower, and both land in the same space. That single fact is what
    makes all four retrieval directions (t2t, t2i, i2i, i2t) fall out of one
    interface rather than needing special cases.
    """

    # ── Vision tower ─────────────────────────────────────────────────
    @abstractmethod
    async def embed_images(self, images: Sequence[ImageInput]):
        """Embed a batch of images for indexing. Returns an EmbeddingOutput."""
        ...

    async def embed_image_query(self, image: ImageInput):
        """Embed a single image used AS a query (image->image, image->text)."""
        return await self.embed_images([image])

    # ── Space identity ───────────────────────────────────────────────
    @property
    @abstractmethod
    def space(self) -> EmbeddingSpace:
        """Resolved AFTER weights load — the dimension may be probed."""
        ...

    @property
    def dense_dim(self) -> int:
        return self.space.dim

    @property
    def supports_sparse(self) -> bool:
        return False  # No CLIP-family model emits lexical vectors.

    # ── Lifecycle ────────────────────────────────────────────────────
    @abstractmethod
    def load(self) -> None:
        """Force weight loading. Called by warmup; otherwise lazy on first use."""
        ...

    @abstractmethod
    async def health_check(self) -> bool: ...


def to_pil(image: ImageInput) -> "PILImage":
    """Normalise any accepted input into an RGB PIL image."""
    from PIL import Image

    if hasattr(image, "convert"):  # already a PIL image
        return image.convert("RGB")  # type: ignore[union-attr]
    if isinstance(image, bytes):
        return Image.open(BytesIO(image)).convert("RGB")
    return Image.open(str(image)).convert("RGB")
