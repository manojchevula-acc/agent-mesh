"""Normalisation applied before hashing, storing and embedding.

Order matters: normalise FIRST, then hash. Otherwise two encodings of the same
picture produce different asset ids and deduplication silently fails.
"""

from io import BytesIO
from typing import TYPE_CHECKING

from ..config.multimodal import AssetStorageConfig

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

# Decompression-bomb guard: a small PNG can decode to gigapixels. Roughly 4x a
# 4K frame — generous for document figures, far below anything dangerous.
_MAX_IMAGE_PIXELS = 64_000_000


def _pil():
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    return Image


def normalize(raw_bytes: bytes, config: AssetStorageConfig) -> tuple[bytes, "PILImage"]:
    """Return canonical bytes plus the decoded image.

    Canonicalisation: EXIF rotation applied, converted to RGB (drops alpha,
    palette and CMYK variance), downscaled to ``max_side_px``, re-encoded in the
    configured format. Two visually identical inputs converge on identical bytes.
    """
    from PIL import ImageOps

    Image = _pil()
    im = Image.open(BytesIO(raw_bytes))
    im = ImageOps.exif_transpose(im) or im
    im = im.convert("RGB")
    if max(im.size) > config.max_side_px:
        im.thumbnail((config.max_side_px, config.max_side_px), Image.LANCZOS)

    buf = BytesIO()
    save_kwargs: dict = {"quality": config.quality}
    if config.image_format.upper() == "WEBP":
        save_kwargs["method"] = 4
    im.save(buf, format=config.image_format, **save_kwargs)
    return buf.getvalue(), im


def make_thumbnail(image: "PILImage", side_px: int, config: AssetStorageConfig) -> bytes:
    Image = _pil()
    thumb = image.copy()
    thumb.thumbnail((side_px, side_px), Image.LANCZOS)
    buf = BytesIO()
    thumb.save(buf, format=config.image_format, quality=80)
    return buf.getvalue()


def to_pil(data: bytes) -> "PILImage":
    """Decode arbitrary image bytes to RGB, with the bomb guard applied."""
    Image = _pil()
    return Image.open(BytesIO(data)).convert("RGB")
