"""MetadataExtractor — derives document metadata from filename + content."""

import re
from pathlib import Path

from ..models.chunk import DocumentType
from ..utils.logging import get_logger

logger = get_logger(__name__)

_TYPE_KEYWORDS: list[tuple[DocumentType, tuple[str, ...]]] = [
    (DocumentType.PRICING_POLICY, ("pricing",)),
    (DocumentType.REGULATORY, ("cbuae", "circular", "regulat")),
    (DocumentType.MRM, ("mrm", "model_risk", "model risk")),
    (DocumentType.RISK_POLICY, ("concentration", "risk", "limit")),
    (DocumentType.PRODUCT_MANUAL, ("product", "manual")),
]

_DATE_PATTERNS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"effective\s+(?:date[:\s]+)?(\d{1,2}\s+\w+\s+\d{4})", re.IGNORECASE),
]


class MetadataExtractor:
    """Infers ``document_type`` and ``effective_date`` from filenames and text."""

    def infer_document_type(self, file_path: Path, fallback: str = "other") -> str:
        name = file_path.stem.lower()
        for doc_type, keywords in _TYPE_KEYWORDS:
            if any(kw in name for kw in keywords):
                return doc_type.value
        return fallback

    def infer_effective_date(self, text: str) -> str:
        head = text[:2000]
        for pattern in _DATE_PATTERNS:
            m = pattern.search(head)
            if m:
                return m.group(1)
        return ""

    def build_base_metadata(
        self,
        file_path: Path,
        document_type: str,
        product_applicability: list[str] | None,
        effective_date: str,
        raw_text: str = "",
        original_name: str | None = None,
    ) -> dict[str, object]:
        # Prefer the original uploaded filename over ``file_path`` — uploads are
        # staged in a temp file whose stem (e.g. ``tmpr0tu6eyx``) would otherwise
        # leak into ``document_name`` and the citation source.
        name_source = Path(original_name).stem if original_name else file_path.stem
        resolved_type = document_type or self.infer_document_type(
            Path(original_name) if original_name else file_path
        )
        resolved_date = effective_date or self.infer_effective_date(raw_text)
        return {
            "document_name": name_source,
            "document_type": resolved_type,
            "product_applicability": product_applicability or [],
            "effective_date": resolved_date,
        }
