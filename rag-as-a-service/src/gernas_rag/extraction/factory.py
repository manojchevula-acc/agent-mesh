"""Extractor factory — selects an extractor from config + file type."""

from pathlib import Path

from ..config.chunking import ChunkingConfig, ExtractionStrategy
from ..config.enrichment import EnrichmentConfig
from .base import BaseExtractor


def get_extractor(
    config: ChunkingConfig,
    file_path: Path,
    enrichment: EnrichmentConfig | None = None,
) -> BaseExtractor:
    """Return an extractor for ``file_path`` per the configured strategy.

    ``AUTO`` selects Docling for office/PDF formats and falls back to PyMuPDF for
    anything else. ``enrichment`` (when enabled) makes the Docling extractor
    rasterise picture regions for downstream vision captioning.
    """
    strategy = config.extraction_strategy
    match strategy:
        case ExtractionStrategy.DOCLING:
            from .docling_extractor import DoclingExtractor

            return DoclingExtractor(enrichment)
        case ExtractionStrategy.UNSTRUCTURED:
            from .unstructured_extractor import UnstructuredExtractor

            return UnstructuredExtractor(config)
        case ExtractionStrategy.PYMUPDF:
            from .pymupdf_extractor import PyMuPDFExtractor

            return PyMuPDFExtractor()
        case ExtractionStrategy.AUTO:
            from .docling_extractor import DoclingExtractor

            docling = DoclingExtractor(enrichment)
            if docling.supports(file_path):
                return docling
            from .pymupdf_extractor import PyMuPDFExtractor

            return PyMuPDFExtractor()
        case _:
            raise ValueError(f"Unsupported extraction strategy: {strategy}")
