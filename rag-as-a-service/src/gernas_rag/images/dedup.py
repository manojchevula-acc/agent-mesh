"""Two-tier deduplication: exact (sha256) then perceptual (dHash).

Exact catches the identical logo XObject repeated on every page. Perceptual
catches the same figure re-rendered at a different scale, or a watermark that
differs by a few pixels of anti-aliasing.

dHash is implemented here in ~10 lines rather than pulling in `imagehash`.
"""

from typing import TYPE_CHECKING

from ..config.multimodal import ImageExtractionConfig

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


def dhash(image: "PILImage", hash_size: int = 8) -> str:
    """64-bit difference hash, hex encoded.

    Compares each pixel with its right-hand neighbour on a 9x8 greyscale
    thumbnail, so the result is invariant to scale, format and mild compression.
    """
    import numpy as np
    from PIL import Image

    small = image.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    pixels = np.asarray(small, dtype="int16")
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def hamming(a: str, b: str) -> int:
    """Bit distance between two hex-encoded hashes."""
    if len(a) != len(b):
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


class Deduper:
    """Per-document deduplication.

    Cross-document exact dedup is free and automatic: the asset id IS the content
    hash, so the same figure in two policies resolves to one stored file and one
    vector. This class additionally suppresses *near*-duplicates within a run.
    """

    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config
        self._seen_sha: dict[str, str] = {}
        self._seen_phash: dict[str, str] = {}

    def is_duplicate(self, content_sha: str, phash: str) -> tuple[bool, str | None]:
        if self._c.dedup_exact and content_sha in self._seen_sha:
            return True, self._seen_sha[content_sha]
        if self._c.dedup_perceptual:
            for known, asset_id in self._seen_phash.items():
                if hamming(phash, known) <= self._c.phash_hamming_threshold:
                    return True, asset_id
        return False, None

    def remember(self, content_sha: str, phash: str, asset_id: str) -> None:
        self._seen_sha[content_sha] = asset_id
        self._seen_phash[phash] = asset_id
