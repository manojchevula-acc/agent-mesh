"""Image extractor factory — selects a backend from config + file type."""

from pathlib import Path

from ..config.chunking import ChunkingConfig, ExtractionStrategy
from ..config.multimodal import ImageExtractionBackend, ImageExtractionConfig
from ..utils.logging import get_logger
from .base import BaseImageExtractor

logger = get_logger(__name__)

# Text strategies that resolve to Docling for PDFs, and therefore make
# structural captions and doc.tables available to the image path.
_DOCLING_BACKED = {ExtractionStrategy.DOCLING, ExtractionStrategy.AUTO}


def get_image_extractor(
    config: ImageExtractionConfig,
    chunking: ChunkingConfig | None = None,
    file_path: Path | None = None,
) -> BaseImageExtractor:
    """Return an image extractor per the configured backend.

    ``AUTO`` follows the text pipeline. Both ``docling`` AND ``auto`` text
    strategies resolve to Docling for PDFs, so both select the Docling image
    backend — otherwise ``extract_table_crops`` would be a silent no-op on the
    default configuration, because only the Docling backend iterates
    ``doc.tables`` (PyMuPDF's ``get_images()`` sees raster XObjects only, and a
    PDF table is normally vector strokes plus live text).
    """
    backend = config.backend
    if backend is ImageExtractionBackend.AUTO:
        text_strategy = chunking.extraction_strategy if chunking else None
        backend = (
            ImageExtractionBackend.DOCLING
            if text_strategy in _DOCLING_BACKED
            else ImageExtractionBackend.PYMUPDF
        )
        logger.info(
            "Image backend resolved from text strategy",
            text_strategy=getattr(text_strategy, "value", None),
            backend=backend.value,
        )

    if config.extract_table_crops and backend is ImageExtractionBackend.PYMUPDF:
        # Fail loud rather than silently dropping half of D8.
        logger.warning(
            "extract_table_crops is enabled but the PyMuPDF backend cannot "
            "produce table crops: it finds raster XObjects only, and does not "
            "see doc.tables. Tables will still be indexed as TEXT chunks. Set "
            "multimodal.extraction.backend=docling for table crops.",
        )

    match backend:
        case ImageExtractionBackend.DOCLING:
            from .docling_images import DoclingImageExtractor

            return DoclingImageExtractor(config)
        case ImageExtractionBackend.PYMUPDF:
            from .pymupdf_images import PyMuPDFImageExtractor

            return PyMuPDFImageExtractor(config)
        case _:
            raise ValueError(f"Unsupported image extraction backend: {backend}")
