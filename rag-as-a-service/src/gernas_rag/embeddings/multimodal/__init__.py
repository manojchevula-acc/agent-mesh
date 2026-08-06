"""Multimodal embedding — text and images in one shared vector space.

Knows nothing about documents, Qdrant or FastAPI. Swapping the encoder must not
touch the image extraction package, and vice versa.
"""

from .base import BaseMultimodalEmbedder, ImageInput
from .factory import get_multimodal_embedder

__all__ = ["BaseMultimodalEmbedder", "ImageInput", "get_multimodal_embedder"]
