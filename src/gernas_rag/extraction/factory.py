"""Extractor factory — selects an extractor from config + file type."""

from pathlib import Path

from ..config.chunking import ChunkingConfig, ExtractionStrategy
from .base import BaseExtractor


def get_extractor(config: ChunkingConfig, file_path: Path) -> BaseExtractor:
    """Return an extractor for ``file_path`` per the configured strategy.

    ``AUTO`` selects Docling for office/PDF formats and falls back to PyMuPDF for
    anything else.
    """
    strategy = config.extraction_strategy
    match strategy:
        case ExtractionStrategy.DOCLING:
            from .docling_extractor import DoclingExtractor

            return DoclingExtractor()
        case ExtractionStrategy.UNSTRUCTURED:
            from .unstructured_extractor import UnstructuredExtractor

            return UnstructuredExtractor(config)
        case ExtractionStrategy.PYMUPDF:
            from .pymupdf_extractor import PyMuPDFExtractor

            return PyMuPDFExtractor()
        case ExtractionStrategy.AUTO:
            from .docling_extractor import DoclingExtractor

            docling = DoclingExtractor()
            if docling.supports(file_path):
                return docling
            from .pymupdf_extractor import PyMuPDFExtractor

            return PyMuPDFExtractor()
        case _:
            raise ValueError(f"Unsupported extraction strategy: {strategy}")
