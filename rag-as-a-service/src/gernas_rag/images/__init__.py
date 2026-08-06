"""Image domain services — extraction, filtering, dedup, storage.

Deliberately knows nothing about embeddings: swapping the encoder must not touch
this package, and changing the PDF backend must not touch the embedding package.

Imports are kept shallow here because the whole package is only pulled in when
``multimodal.extraction.enabled`` is true.
"""

from .base import BaseImageExtractor, RawImage

__all__ = ["BaseImageExtractor", "RawImage"]
