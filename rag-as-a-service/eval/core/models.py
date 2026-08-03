"""Schemas for every evaluation artifact — ground truth, run records, reports.

All of these are versioned Pydantic models rather than loose dicts so a
hand-edited ground-truth file fails loudly at load time instead of producing a
quietly wrong metric. ``version`` is on every top-level container so a future
schema change can migrate rather than silently misread old files.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# A judgment at or above this grade is treated as "contains the answer" for
# binary metrics (recall / MRR). Lower positive grades still contribute gain to
# nDCG, which is what makes graded judgments worth collecting at all.
RELEVANT_GRADE = 2


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════
# Stage 1 — layout manifest (ground truth for extraction)
# ══════════════════════════════════════════════════════════════════════
class ManifestFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    kind: str = ""  # e.g. "line_chart", "org_chart", "timeline"
    description: str = ""
    must_detect: bool = True  # False for decorative art that is fine to skip.


class ManifestTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int
    title: str = ""
    rows: int | None = None  # Including the header row, when known.
    cols: int | None = None
    must_detect: bool = True


class ManifestDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str  # Must equal ChunkMetadata.document_name for cross-stage joins.
    file: str  # Path relative to the repo root.
    pages: int | None = None
    scanned: bool = False  # Expected OCR routing; asserted against text-layer detection.
    figures: list[ManifestFigure] = Field(default_factory=list)
    tables: list[ManifestTable] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    # Scaffolded entries start unverified. Unverified documents are reported but
    # never gate CI — otherwise the first `--init` run would "pass" against
    # ground truth that is really just a copy of the system's own output.
    verified: bool = False
    notes: str = ""


class LayoutManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    documents: list[ManifestDocument] = Field(default_factory=list)

    def by_document(self) -> dict[str, ManifestDocument]:
        return {d.document: d for d in self.documents}


# ══════════════════════════════════════════════════════════════════════
# Stage 2 — figure transcriptions (ground truth for VLM captions)
# ══════════════════════════════════════════════════════════════════════
class FigureTranscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Content-addressed, so a transcription survives re-ingestion as long as the
    # underlying image bytes are unchanged.
    artifact_ref: str
    document: str = ""
    page: int | None = None
    modality: str = "figure"
    # The human ground truth. Empty + verified=False means "not yet transcribed".
    transcription: str = ""
    # What the VLM produced when this entry was scaffolded. Reference only — never
    # scored against, or the metric would be comparing the system to itself.
    vlm_caption: str = ""
    verified: bool = False
    notes: str = ""


class TranscriptionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    items: list[FigureTranscription] = Field(default_factory=list)

    def verified_by_ref(self) -> dict[str, FigureTranscription]:
        return {i.artifact_ref: i for i in self.items if i.verified and i.transcription.strip()}

    def by_ref(self) -> dict[str, FigureTranscription]:
        return {i.artifact_ref: i for i in self.items}


# ══════════════════════════════════════════════════════════════════════
# Stage 3 — graded relevance judgments (qrels)
# ══════════════════════════════════════════════════════════════════════
class RelevanceJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # chunk_id is the only stable identity: deterministic, unique, and unlike
    # clause_reference it is not derived from caption text (see stage 2 findings).
    chunk_id: str
    grade: int = Field(ge=0, le=3)
    # Denormalised for human review of the qrels file; never used for matching.
    document: str = ""
    modality: str = "text"
    clause_reference: str = ""
    note: str = ""


class QrelQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    question: str
    answerable: bool = True
    judgments: list[RelevanceJudgment] = Field(default_factory=list)
    verified: bool = False
    ambiguous: bool = False  # Set by the deriver when a gold source matched >1 chunk.

    @property
    def graded(self) -> dict[str, int]:
        return {j.chunk_id: j.grade for j in self.judgments if j.grade > 0}

    @property
    def relevant_ids(self) -> set[str]:
        return {j.chunk_id for j in self.judgments if j.grade >= RELEVANT_GRADE}

    @property
    def modalities(self) -> set[str]:
        return {j.modality for j in self.judgments if j.grade >= RELEVANT_GRADE}


class QrelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    questions: list[QrelQuestion] = Field(default_factory=list)

    def by_id(self) -> dict[str, QrelQuestion]:
        return {q.id: q for q in self.questions}


# ══════════════════════════════════════════════════════════════════════
# Stage 3 — retrieval run records
# ══════════════════════════════════════════════════════════════════════
class RankedHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    rank: int  # 0-based position in this stage's output.
    score: float
    document: str = ""
    modality: str = "text"
    clause_reference: str = ""
    is_parent: bool = False


class RetrievalStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    hits: list[RankedHit] = Field(default_factory=list)
    latency_ms: float = 0.0

    @property
    def ranked_ids(self) -> list[str]:
        return [h.chunk_id for h in self.hits]


class RetrievalRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    stages: dict[str, RetrievalStageResult] = Field(default_factory=dict)
    # True when the locally reproduced "final" ordering matched what
    # RetrievalPipeline.retrieve() actually returned. False means the ablation
    # has drifted from production and its numbers must not be trusted.
    pipeline_parity: bool | None = None
    generated_at: str = Field(default_factory=utc_now_iso)


class RetrievalRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    generated_at: str = Field(default_factory=utc_now_iso)
    config: dict[str, Any] = Field(default_factory=dict)
    records: list[RetrievalRunRecord] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════
# Stage 4 — generation run records
# ══════════════════════════════════════════════════════════════════════
class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 1-based, matching the [N] block numbering the generator puts in the prompt.
    # Citation checking is only meaningful because this mirrors that numbering.
    index: int
    chunk_id: str = ""
    document: str = ""
    modality: str = "text"
    clause_reference: str = ""
    artifact_ref: str | None = None
    text: str = ""
    parent_text: str | None = None
    score: float = 0.0


class GenerationRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    answer: str
    contexts: list[GenerationContext] = Field(default_factory=list)
    images_hydrated: int = 0
    vision_used: bool = False
    hydration_eligible: int = 0
    retrieval_latency_ms: float = 0.0
    generation_latency_ms: float = 0.0
    top_k: int = 5
    generated_at: str = Field(default_factory=utc_now_iso)


class GenerationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    generated_at: str = Field(default_factory=utc_now_iso)
    config: dict[str, Any] = Field(default_factory=dict)
    records: list[GenerationRunRecord] = Field(default_factory=list)

    def by_id(self) -> dict[str, GenerationRunRecord]:
        return {r.id: r for r in self.records}


# ══════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════
Severity = Literal["error", "warn", "info"]
Direction = Literal["min", "max"]


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | None
    threshold: float | None = None
    # "min" = value must be >= threshold; "max" = value must be <= threshold.
    direction: Direction = "min"
    unit: str = ""
    detail: str = ""
    # None when ungated or when there was no data to compute the metric from —
    # deliberately distinct from False so "no evidence" never reads as "passed".
    passed: bool | None = None
    gating: bool = True

    @classmethod
    def build(
        cls,
        name: str,
        value: float | None,
        threshold: float | None = None,
        direction: Direction = "min",
        unit: str = "",
        detail: str = "",
        gating: bool = True,
    ) -> "MetricResult":
        passed: bool | None = None
        if value is not None and threshold is not None:
            passed = value >= threshold if direction == "min" else value <= threshold
        return cls(
            name=name,
            value=value,
            threshold=threshold,
            direction=direction,
            unit=unit,
            detail=detail,
            passed=passed,
            gating=gating,
        )


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    code: str  # Stable, greppable, e.g. "ORPHAN_ARTIFACT".
    subject: str = ""  # The thing at fault: chunk id, document, artifact ref.
    message: str = ""


class ReportTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    columns: list[str]
    rows: list[list[str]] = Field(default_factory=list)
    note: str = ""


class StageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    title: str
    generated_at: str = Field(default_factory=utc_now_iso)
    summary: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    metrics: list[MetricResult] = Field(default_factory=list)
    tables: list[ReportTable] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    def add_metric(self, metric: MetricResult) -> None:
        self.metrics.append(metric)

    def add_finding(self, severity: Severity, code: str, subject: str = "", message: str = "") -> None:
        self.findings.append(Finding(severity=severity, code=code, subject=subject, message=message))

    @property
    def failed_metrics(self) -> list[MetricResult]:
        return [m for m in self.metrics if m.gating and m.passed is False]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]
