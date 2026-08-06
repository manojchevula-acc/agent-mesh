"""Turn a RetrievedImage into an ImagePart.

Three jobs, all of which matter:
  1. FORMAT - the asset store holds WEBP; Groq's documented example uses
     image/jpeg. Convert rather than gamble on WEBP support.
  2. SIZE   - downscale to vision_image_max_side_px. This is the main lever on
     image token cost.
  3. BUDGET - hard-cap the number of images at vision_max_images.
"""

import base64
from io import BytesIO

from ..config.llm import LLMConfig
from ..images.store import BaseAssetStore
from ..llm.base import ImagePart
from ..models.retrieval import RetrievedImage
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ImagePayloadBuilder:
    def __init__(self, asset_store: BaseAssetStore, config: LLMConfig) -> None:
        self._store = asset_store
        self._c = config

    def build(self, image: RetrievedImage) -> ImagePart | None:
        try:
            raw = self._store.get(image.asset_id)
        except (FileNotFoundError, ValueError, OSError) as exc:
            # Never fail generation over one missing asset.
            logger.warning(
                "Asset unavailable; skipping in prompt",
                asset_id=image.asset_id,
                error=str(exc),
            )
            return None

        try:
            from PIL import Image

            im = Image.open(BytesIO(raw)).convert("RGB")
            limit = self._c.vision_image_max_side_px
            if max(im.size) > limit:
                im.thumbnail((limit, limit), Image.LANCZOS)

            buf = BytesIO()
            fmt = self._c.vision_image_format.upper()
            im.save(buf, format=fmt, quality=self._c.vision_image_quality)
            encoded = base64.b64encode(buf.getvalue()).decode()
            mime = f"image/{'jpeg' if fmt == 'JPEG' else fmt.lower()}"
            return ImagePart(data_uri=f"data:{mime};base64,{encoded}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Image payload build failed", asset_id=image.asset_id, error=str(exc)
            )
            return None

    def build_all(
        self, images: list[RetrievedImage]
    ) -> list[tuple[RetrievedImage, ImagePart]]:
        out: list[tuple[RetrievedImage, ImagePart]] = []
        for image in images[: self._c.vision_max_images]:  # HARD cap
            part = self.build(image)
            if part is not None:
                out.append((image, part))
        return out
