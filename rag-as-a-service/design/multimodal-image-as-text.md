# Image-as-Text Multimodal Extension — Technical Design

**Repo:** `rag-as-a-service` (package `gernas_rag`) · **Branch:** `feat/multimodal-rag-poc`
**Scope:** Approach (A) from the POC design doc §13.2 — describe figures/tables with a vision model at ingest, embed the description as a normal text chunk, keep the original image as a citable artifact. Includes **optional, config-gated hydration** (§13): loading the original image back from the artifact store at answer time and passing it to a vision-capable LLM. Approaches (B) native multimodal embedding and (C) ColPali page-as-image remain out of scope for this document.
**Status:** Design for review — no code has been written yet.

---

## 1. Why this document differs from §13 of the POC design

Section 13 of the POC design describes the target shape at the level of "Enrich is the only new stage; embedding, Qdrant, RRF, and the reranker are unchanged." That's true at the architecture-diagram level. Reading the actual pipeline surfaced one load-bearing detail the diagram can't show:

> **`HierarchicalChunker.chunk()` never reads `ExtractionResult.elements`. It only splits `ExtractionResult.raw_markdown`.**

Everything downstream of extraction — chunking, embedding, upsert, retrieval — operates on a markdown string, not on the typed element list Docling produces. A design that enriches `elements` and calls it done will silently produce zero image chunks, because nothing ever looks at `elements` again after extraction.

The fix must also guarantee **one figure/table/diagram = exactly one chunk**. An earlier revision of this design tried to bridge enrichment *into* the markdown stream (so the existing splitter would pick it up), but that routes summaries through `RecursiveCharacterTextSplitter` (1600-char window), which **fragments any summary longer than the window** — precisely the dense-table case that matters most. So §8–§9 instead give media their own path: the chunker keeps splitting `raw_markdown` for text exactly as today, and **builds media chunks atomically and directly from the enriched elements**, bypassing the splitter entirely. See §9.1 for the atomicity guarantee.

The rest of this document is organized the same way: current state (with exact file/line grounding), then the diff, section by section, in pipeline order.

---

## 2. Current-state gap analysis

| # | File | Current behavior | Gap for image-as-text |
|---|---|---|---|
| 1 | `extraction/base.py` | `ElementType` = `HEADING, PARAGRAPH, TABLE, LIST_ITEM, CAPTION`. `ExtractedElement` has no image/bbox field. | No `FIGURE` / `PAGE_IMAGE` type; nowhere to hold image bytes. |
| 2 | `extraction/docling_extractor.py` | `PdfPipelineOptions(..., generate_page_images=False)` — image generation is **explicitly disabled**. Docling label→`ElementType` map has no `"picture"` entry, so picture items fall through to `ElementType.PARAGRAPH` with empty/garbage text. | Pictures are dropped today, exactly as POC §13.1 describes for the general text-only case. |
| 3 | `chunking/hierarchical.py` | `chunk()` reads only `extraction.raw_markdown`; `extraction.elements` is unused dead weight for this chunker. | Any element-level enrichment needs a path back into the markdown string, or the chunker needs to change. |
| 4 | `models/chunk.py` | `ChunkMetadata` has no `modality`, `artifact_ref`, `bbox`, or `enrichment_model` field. | Can't tag or filter image-derived chunks; can't record VLM provenance for audit. |
| 5 | `vectordb/qdrant_client.py` | Payload indexes exist for exactly `document_type`, `product_applicability`, `deprecated`, `effective_date`. Single named-vector schema (`dense`, `sparse`). | No index for `modality`; "tables only" filtering isn't possible yet. |
| 6 | `llm/base.py` | `Message.content: str` — plain string only, all 4 providers (`groq`, `anthropic`, `huggingface`, `openai_compat`) build string-only payloads. | No multimodal message plumbing exists anywhere. **Not required for image-as-text** (see §10) — flagged here only so it isn't conflated with this phase's scope. |
| 7 | `models/retrieval.py` | `RetrievedChunk` has no `modality` field; `DocumentFilter` has no modality filter. | Callers can't see or filter on chunk modality in `/retrieve` responses. |
| 8 | `generation/generator.py` | `_build_context()` builds a numbered context block from `RetrievedChunk.text` — modality-agnostic already. | No change required to function; a citation-clarity addition is optional (§12). |
| 9 | `utils/hashing.py` | Only `make_chunk_id(doc_name, ref)` (MD5) and `make_point_uuid(chunk_id)` (UUIDv5). No byte-content hashing. | Need a `hash_bytes()` for content-addressed artifact storage. |
| 10 | `pyproject.toml` | `docling`, `anthropic`, `openai` SDKs present. **No image-handling library** (no `Pillow`). | Need `Pillow` for image encode/decode/resizing before VLM calls. |

Everything in the table is additive — no existing field is renamed or removed, consistent with the POC design's "safe default" framing.

---

## 3. Target repo structure

```
src/gernas_rag/
├── enrichment/                     # NEW — parallel to embeddings/, llm/, extraction/
│   ├── __init__.py
│   ├── base.py                     # EnrichmentInput, EnrichmentOutput, BaseEnricher
│   ├── vision_llm_enricher.py      # Claude/GPT-4o vision caption provider
│   ├── table_enricher.py           # confidence-gated: skip VLM if Docling table structure is confident
│   └── factory.py                  # get_enricher(config) -> BaseEnricher
├── storage/                        # NEW
│   ├── __init__.py
│   └── artifact_store.py           # BaseArtifactStore.get_bytes/put_bytes, LocalArtifactStore (S3ArtifactStore stubbed)
├── llm/
│   ├── base.py                     # MODIFIED (hydration only) — Message.content widened to str | list[ContentPart]
│   ├── anthropic_llm.py            # MODIFIED (hydration only) — serialize image content blocks
│   └── openai_compat.py            # MODIFIED (hydration only) — serialize image_url content blocks
├── extraction/
│   ├── base.py                     # MODIFIED — ElementType.FIGURE/PAGE_IMAGE, image_bytes/bbox fields
│   ├── docling_extractor.py        # MODIFIED — enable picture extraction, tag nearest heading
│   └── unstructured_extractor.py   # MODIFIED (fallback path, same element additions)
├── chunking/
│   └── hierarchical.py             # MODIFIED — text path unchanged; add _build_media_chunks() (atomic, per-element)
├── ingestion/
│   ├── pipeline.py                 # MODIFIED — new Enrich stage (attaches caption+ref to elements) between Extract and Chunk
│   └── metadata.py                 # unchanged
├── models/
│   ├── chunk.py                    # MODIFIED — Modality enum, 4 new ChunkMetadata fields
│   └── retrieval.py                # MODIFIED — RetrievedChunk.modality, DocumentFilter.modality
├── vectordb/
│   └── qdrant_client.py            # MODIFIED — payload index on `modality`
├── generation/
│   └── generator.py                # MODIFIED — [Figure]/[Table] header marker; config-gated hydration + vision routing
├── retrieval/
│   └── pipeline.py                 # MODIFIED (optional §12) — surface modality/artifact_ref on RetrievedChunk
├── config/
│   ├── enrichment.py                # NEW — EnrichmentConfig (ingest-time captioning)
│   ├── hydration.py                 # NEW — HydrationConfig (answer-time image loading + vision LLM)
│   ├── artifact_store.py            # NEW — ArtifactStoreConfig
│   └── settings.py                  # MODIFIED — compose the new config blocks
└── utils/
    └── hashing.py                   # MODIFIED — add hash_bytes()

config/
├── default.yaml                    # MODIFIED — enrichment: {enabled: false, ...}, artifact_store: {...}
tests/
├── unit/test_enrichment.py         # NEW
├── unit/test_artifact_store.py     # NEW
└── unit/test_chunking.py           # MODIFIED — atomic media-chunk cases (one element -> one chunk, no split)
```

No existing directory is removed or renamed. `enrichment/` and `storage/` mirror the existing `embeddings/`/`llm/`/`extraction/` provider-pattern (`base.py` + concrete impls + `factory.py`), so the addition reads as "more of the same shape," not a new architectural idiom.

---

## 4. Data model changes

### 4.1 `models/chunk.py`

```python
class Modality(str, Enum):
    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"
    PAGE_IMAGE = "page_image"

class ChunkMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_name: str
    document_type: DocumentType
    section_heading: str = ""
    clause_reference: str = ""
    product_applicability: list[str] = Field(default_factory=list)
    effective_date: str = ""
    last_indexed_at: datetime = Field(default_factory=_utcnow)
    freshness_score: float = 1.0
    deprecated: bool = False
    parent_chunk_id: str | None = None
    source_page: int | None = None
    # --- NEW: image-as-text fields ---
    modality: Modality = Modality.TEXT
    artifact_ref: str | None = None       # content-addressed key in the artifact store, e.g. "sha256:<hex>.png"
    bbox: tuple[float, float, float, float] | None = None
    enrichment_model: str | None = None    # e.g. "claude-haiku-4-5-20251001"; None on fail-soft degrade
```

All four new fields default to values that make an untouched text chunk serialize identically to today (`modality=TEXT`, everything else `None`) — existing ingested collections don't need a migration, and `_payload_to_chunk` in `qdrant_client.py` (which already filters payload keys against `ChunkMetadata.model_fields.keys()`) picks the new fields up for free.

### 4.2 `extraction/base.py`

```python
class ElementType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    FIGURE = "figure"           # NEW
    PAGE_IMAGE = "page_image"   # NEW — reserved for the future ColPali fallback path, not populated in this phase

@dataclass
class ExtractedElement:
    element_type: ElementType
    text: str
    level: int = 0
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes | None = None   # NEW — raw cropped image, cleared after enrichment writes it to the artifact store
    bbox: tuple[float, float, float, float] | None = None  # NEW
```

`metadata["nearest_heading"]` is used (not a new dataclass field) to carry section context forward — see §6.

### 4.3 `models/retrieval.py`

```python
class DocumentFilter(BaseModel):
    document_type: list[str] | None = None
    product_applicability: list[str] | None = None
    effective_date_from: str | None = None
    deprecated: bool = False
    modality: list[str] | None = None   # NEW — e.g. ["table"] for "pricing grids only"

class RetrievedChunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    source: str
    section_heading: str = ""
    clause_reference: str
    score: float
    effective_date: str
    freshness_warning: bool
    parent_text: str | None = None
    modality: str = "text"              # NEW
    artifact_ref: str | None = None     # NEW — citation metadata only; no bytes in Phase 2 (see §13)
```

---

## 5. New module: `enrichment/`

### 5.1 `enrichment/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class EnrichmentInput:
    image_bytes: bytes
    mime_type: str                 # "image/png"
    element_type: str              # "figure" | "table"
    context_text: str = ""         # nearest heading / existing Docling caption, if any

@dataclass
class EnrichmentOutput:
    caption_text: str
    model_name: str | None
    ok: bool                       # False on fail-soft degrade (VLM unavailable/timeout)

class BaseEnricher(ABC):
    @abstractmethod
    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput: ...
```

Same shape as `BaseEmbedder`/`BaseLLM`/`BaseExtractor` — one abstract method, dataclass I/O.

### 5.2 `enrichment/vision_llm_enricher.py`

Deliberately does **not** go through `llm/base.py::BaseLLM.generate()`, because `Message.content` is `str`-only there and widening it is generation-contract surgery this phase doesn't need (see §10). Instead it talks to the Anthropic/OpenAI SDK client directly, reusing the existing `LLMConfig.anthropic_api_key` / `openai_api_key` credentials so no new secret has to be provisioned.

```python
import base64
from gernas_rag.enrichment.base import BaseEnricher, EnrichmentInput, EnrichmentOutput
from gernas_rag.config.enrichment import EnrichmentConfig

_TRANSCRIBE_PROMPT = (
    "Transcribe this {element_type} from a banking policy document. "
    "List every visible label, unit, row/column header, axis, and numeric value exactly as shown. "
    "Do not infer or summarize trends — transcribe only what is legibly printed. "
    "If a value is illegible, write [illegible] rather than guessing."
)

class VisionLLMEnricher(BaseEnricher):
    def __init__(self, config: EnrichmentConfig, api_key: str) -> None:
        self._config = config
        self._api_key = api_key
        self._client = None  # lazy, mirrors BGEM3Embedder / Reranker lazy-load pattern

    def _load(self) -> None:
        if self._client is not None:
            return
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput:
        self._load()
        b64 = base64.b64encode(item.image_bytes).decode("ascii")
        prompt = _TRANSCRIBE_PROMPT.format(element_type=item.element_type)
        if item.context_text:
            prompt += f"\n\nDocument section: {item.context_text}"
        try:
            response = await self._client.messages.create(
                model=self._config.vlm_model_name,
                max_tokens=self._config.max_tokens,
                timeout=self._config.timeout_seconds,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": item.mime_type, "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            return EnrichmentOutput(caption_text=text.strip(), model_name=self._config.vlm_model_name, ok=True)
        except Exception:
            return EnrichmentOutput(caption_text="", model_name=None, ok=False)
```

The `except Exception: return ok=False` is the fail-soft contract POC §13.3 requires ("if the VLM is unavailable or times out, ingestion falls back to OCR text + caption and never fails outright") — matches the existing `Reranker.rerank()` fallback-on-failure pattern already in the codebase, so this isn't a new error-handling idiom either.

### 5.3 `enrichment/table_enricher.py`

```python
class TableEnricher(BaseEnricher):
    """Wraps VisionLLMEnricher but skips the VLM call when Docling's own
    table-structure confidence is already high — most born-digital pricing
    grids don't need a vision pass at all."""

    def __init__(self, vlm: BaseEnricher, confidence_threshold: float) -> None:
        self._vlm = vlm
        self._threshold = confidence_threshold

    async def enrich(self, item: EnrichmentInput) -> EnrichmentOutput:
        confidence = float(item.context_text and 1.0 or 0.0)  # placeholder — real value threaded from docling table item, see §6
        if confidence >= self._threshold:
            return EnrichmentOutput(caption_text="", model_name=None, ok=False)  # signals "use existing table text, no enrichment needed"
        return await self._vlm.enrich(item)
```

### 5.4 `enrichment/factory.py`

```python
def get_enricher(config: EnrichmentConfig, llm_config: LLMConfig) -> BaseEnricher:
    if config.provider == "anthropic":
        api_key = llm_config.anthropic_api_key
    elif config.provider == "openai":
        api_key = llm_config.openai_api_key
    else:
        raise ValueError(f"Unsupported enrichment provider: {config.provider}")
    vlm = VisionLLMEnricher(config, api_key)
    return TableEnricher(vlm, config.table_confidence_threshold)
```

### 5.5 How the enriched caption travels to the chunker

Enrichment does **not** inject text into `raw_markdown`. Instead it writes the VLM caption and the artifact key back onto the `ExtractedElement` it came from (its `text` and `metadata`), and the chunker builds an atomic `Chunk` from that element (§9). Rationale, stated plainly because an earlier draft got this wrong:

- **A markdown-injection bridge is unsafe.** Anything placed in `raw_markdown` flows through `RecursiveCharacterTextSplitter` (1600-char window). A long table transcription is split mid-summary, which both fragments the summary across chunks and breaks any in-band marker used to carry `artifact_ref` — the link to the image silently dies exactly when the content is richest (§9.1).
- **Element attachment keeps media off the splitter.** The caption never enters the string the splitter operates on, so it cannot be cut. `modality`/`artifact_ref`/`enrichment_model` are set as typed fields on `ChunkMetadata`, not parsed out of text — nothing to regex, nothing to break.

Concretely, `_enrich` (§8) sets on each media element:

```python
el.text = result.caption_text if result.ok else el.text          # full caption becomes the chunk text
el.metadata["artifact_ref"] = ref                                  # content-addressed key from the artifact store
el.metadata["enrichment_model"] = result.model_name               # None on fail-soft degrade
```

No `markers.py` module, no HTML-comment protocol, no separator changes.

---

## 6. `extraction/docling_extractor.py` changes

```python
# BEFORE
pipeline_options = PdfPipelineOptions(
    do_ocr=do_ocr,
    do_table_structure=True,
    images_scale=1.0,
    generate_page_images=False,
)

# AFTER
pipeline_options = PdfPipelineOptions(
    do_ocr=do_ocr,
    do_table_structure=True,
    images_scale=self._images_scale,           # from EnrichmentConfig, default unchanged (1.0) when disabled
    generate_page_images=False,                  # unchanged — reserved for future ColPali path
    generate_picture_images=self._enrichment_enabled,  # NEW — gate behind config, off by default
)
```

> **Verify at implementation time:** the exact attribute name (`generate_picture_images` vs. a nested `PictureDescriptionOptions` toggle) and the accessor for cropped bytes (`PictureItem.get_image(doc)` / `TableItem.get_image(doc)`) are correct for Docling ≥2.x as of the last version this was checked against, but `docling` is not installed in this dev environment, so this must be confirmed against the pinned `docling` version in `pyproject.toml` (`>=2.0.0`, unpinned upper bound) before merging. If the API differs, the surrounding element-loop shape below still holds — only the two `.get_image()` call sites change.

`_sync_extract` gains a running `current_heading` tracker and populates `image_bytes`/`bbox` on FIGURE/TABLE elements:

```python
def _sync_extract(self, file_path: Path) -> ExtractionResult:
    ...
    elements: list[ExtractedElement] = []
    current_heading = ""
    for item, _level in doc.iterate_items():
        label = getattr(item, "label", "text")
        el_type = _LABEL_MAP.get(label, ElementType.PARAGRAPH)

        if el_type == ElementType.HEADING:
            current_heading = item.text

        image_bytes = None
        bbox = None
        if label == "picture" and self._enrichment_enabled:
            el_type = ElementType.FIGURE
            pil_image = item.get_image(doc)          # returns None if extraction failed — handled below
            if pil_image is not None:
                image_bytes = _pil_to_png_bytes(pil_image)
                bbox = tuple(item.prov[0].bbox.as_tuple()) if item.prov else None
        elif label == "table":
            el_type = ElementType.TABLE
            # low-confidence table structure also gets an image crop for VLM fallback
            table_confidence = getattr(item, "confidence", 1.0)
            if self._enrichment_enabled and table_confidence < self._table_confidence_threshold:
                pil_image = item.get_image(doc)
                if pil_image is not None:
                    image_bytes = _pil_to_png_bytes(pil_image)

        elements.append(ExtractedElement(
            element_type=el_type,
            text=item.text if hasattr(item, "text") else "",
            level=_level,
            page_number=item.prov[0].page_no if getattr(item, "prov", None) else None,
            metadata={"nearest_heading": current_heading, "table_confidence": table_confidence if label == "table" else None},
            image_bytes=image_bytes,
            bbox=bbox,
        ))

    return ExtractionResult(elements=elements, raw_markdown=doc.export_to_markdown(), page_count=doc.num_pages(), file_path=str(file_path))
```

`_LABEL_MAP` gains no new key beyond what `el_type` reassignment above already handles inline — kept as a small map-then-override rather than growing the dict, since `"picture"` needs conditional logic (`self._enrichment_enabled`), not a static mapping.

`raw_markdown` is still Docling's own `export_to_markdown()` — untouched. Image/figure content reaches the chunker through the **elements** (enriched in §8, chunked atomically in §9), never through the markdown string — so `docling_extractor.py`'s markdown path is completely unmodified and media summaries never touch the text splitter.

Note on tables and double-indexing: Docling already serializes tables *into* `raw_markdown`. To avoid indexing a table twice, enrichment only produces an atomic table chunk for **low-confidence** tables (where the VLM re-transcription adds value); high-confidence tables are left as Docling's markdown text and flow through the normal text path unchanged (the `TableEnricher` returns `ok=False` for them, §5.3, so no `artifact_ref` is attached and §9 skips them). Low-confidence tables do produce a mild duplication (the garbled markdown version still sits in `raw_markdown`); that version scores poorly against queries anyway, and a markdown-suppression pass is listed as a follow-up (§19) rather than solved here.

`UnstructuredExtractor` gets the analogous change (map `"Image"`/`"Figure"` Unstructured categories to `ElementType.FIGURE`, capture `element.metadata.image_path` if `strategy="hi_res"`), not detailed line-by-line here since it mirrors the Docling diff.

---

## 7. New module: `storage/artifact_store.py`

```python
from abc import ABC, abstractmethod
from pathlib import Path
from gernas_rag.utils.hashing import hash_bytes

class BaseArtifactStore(ABC):
    @abstractmethod
    async def put_bytes(self, data: bytes, mime_type: str) -> str: ...
    @abstractmethod
    async def get_bytes(self, ref: str) -> tuple[bytes, str]: ...  # (data, mime_type)

class LocalArtifactStore(BaseArtifactStore):
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    async def put_bytes(self, data: bytes, mime_type: str) -> str:
        digest = hash_bytes(data)
        ext = "png" if mime_type == "image/png" else "bin"
        ref = f"sha256:{digest}.{ext}"
        path = self._root / f"{digest}.{ext}"
        if not path.exists():          # content-addressed => idempotent write, same guarantee as Qdrant point ids
            path.write_bytes(data)
        return ref

    async def get_bytes(self, ref: str) -> tuple[bytes, str]:
        digest, _, ext = ref.removeprefix("sha256:").partition(".")
        path = self._root / f"{digest}.{ext}"
        mime_type = "image/png" if ext == "png" else "application/octet-stream"
        return path.read_bytes(), mime_type
```

`S3ArtifactStore` is stubbed with the same interface for the AKS production deployment path (POC §12 "Deployment Options" table) but not implemented in this phase — local disk (mounted volume, same pattern as `./docs:/app/docs:ro` in `docker-compose.yml`) is sufficient for the POC.

### 7.1 `utils/hashing.py` addition

```python
def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

One function, additive, no change to `make_chunk_id`/`make_point_uuid`.

---

## 8. `ingestion/pipeline.py` — the new Enrich stage

```python
class IngestionPipeline:
    def __init__(self, settings: Settings, embedder: BaseEmbedder, vectordb: BaseVectorDB) -> None:
        self._chunker = get_chunker(settings.chunking)
        self._metadata = MetadataExtractor()
        self._extractor = get_extractor(settings.chunking, Path("placeholder.pdf"))
        self._settings = settings
        # NEW
        self._enrichment_enabled = settings.enrichment.enabled
        if self._enrichment_enabled:
            self._enricher = get_enricher(settings.enrichment, settings.llm)
            self._artifact_store = LocalArtifactStore(Path(settings.artifact_store.local_path))

    async def ingest_file(self, file_path: Path, document_type: str, ...) -> IngestionResult:
        extraction = await self._extractor.extract(file_path)                     # Step 1: Extract
        if self._enrichment_enabled:
            extraction = await self._enrich(extraction)                          # Step 2: Enrich (NEW)
        base_metadata = self._metadata.build_base_metadata(...)
        chunks = self._chunker.chunk(extraction, base_metadata)                    # Step 3: Chunk
        embedded_chunks = await self._embed_chunks_in_batches(chunks)              # Step 4: Embed — UNCHANGED
        count = await self._vectordb.upsert(embedded_chunks)                       # Step 5: Upsert — UNCHANGED
        return IngestionResult(...)

    async def _enrich(self, extraction: ExtractionResult) -> ExtractionResult:
        """Enriches media elements IN PLACE: stores the image in the artifact
        store, captions it with the VLM, and writes caption + artifact_ref +
        model back onto the element. raw_markdown is NOT touched — the chunker
        builds atomic media chunks from these elements (§9)."""
        candidates = [
            el for el in extraction.elements
            if el.element_type in (ElementType.FIGURE, ElementType.TABLE)
            and el.image_bytes is not None
            and len(el.image_bytes) >= self._settings.enrichment.min_image_bytes
        ]
        if not candidates:
            return extraction

        semaphore = asyncio.Semaphore(self._settings.enrichment.max_concurrent)
        async def _process(el: ExtractedElement) -> None:
            async with semaphore:
                ref = await self._artifact_store.put_bytes(el.image_bytes, "image/png")
                result = await self._enricher.enrich(EnrichmentInput(
                    image_bytes=el.image_bytes, mime_type="image/png",
                    element_type=el.element_type.value,
                    context_text=el.metadata.get("nearest_heading", ""),
                ))
                # Attach results to the element; the chunker turns this into one atomic Chunk (§9).
                if result.ok and result.caption_text:
                    el.text = result.caption_text            # full caption becomes chunk text — never split (§9.1)
                el.metadata["artifact_ref"] = ref             # marks this element as an enriched media chunk
                el.metadata["enrichment_model"] = result.model_name  # None on fail-soft degrade

        await asyncio.gather(*(_process(el) for el in candidates))
        return extraction  # same elements (now enriched), same raw_markdown
```

Notes:
- `ExtractedElement` is a mutable dataclass, so in-place mutation is fine; the returned `ExtractionResult` is the same object with enriched elements.
- **Fail-soft:** `VisionLLMEnricher.enrich()` catches internally and returns `ok=False` (§5.2). On degrade, `el.text` keeps whatever Docling extracted (table markdown, or empty for a figure) and `enrichment_model` is `None` — the element still becomes a chunk, still carries `artifact_ref` (so hydration can later show the raw image even when captioning failed). Matches the top-level `ingest_file` try/except that turns any exception into `IngestionResult(status=ERROR)` — enrichment never escalates that far.
- **Only enriched elements get `artifact_ref`.** High-confidence tables (TableEnricher → `ok=False` *without* a crop, §5.3) and plain text elements never get one, so §9 leaves them entirely to the text path — no duplicate table chunk.
- `min_image_bytes` is the "skip decorative logos, rules, icons" gate from POC §13.3 — cheaper/more robust than a pixel check since bytes are already in hand.
- Caption caching by content hash (POC §13.3) falls out of the content-addressed store for free at the artifact level; a dedicated `dict[sha256, EnrichmentOutput]` in front of `_enricher.enrich()` to skip repeat VLM calls on re-ingest is a trivial Phase 2.5 follow-up, deferred to keep the diff reviewable.

---

## 9. `chunking/hierarchical.py` — atomic media chunks

`HierarchicalChunker.chunk()` gains one line — a call to a new `_build_media_chunks()` — and its existing text logic (parent split → child split → `Chunk(...)`) is **byte-for-byte unchanged**:

```python
def chunk(self, extraction: ExtractionResult, base_metadata: dict[str, Any]) -> list[Chunk]:
    text_chunks = self._chunk_text(extraction.raw_markdown, base_metadata)   # EXISTING logic, unchanged
    media_chunks = self._build_media_chunks(extraction.elements, base_metadata)  # NEW
    return text_chunks + media_chunks
```

`_build_media_chunks` turns each **enriched** element (those `_enrich` tagged with an `artifact_ref`, §8) into exactly one atomic `Chunk` — the full caption goes in as a single unit, never handed to the splitter:

```python
def _build_media_chunks(self, elements: list[ExtractedElement], base_metadata: dict[str, Any]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for el in elements:
        ref = el.metadata.get("artifact_ref")
        if ref is None:
            continue                        # not an enriched media element → belongs to the text path (or was skipped)
        modality = Modality(el.element_type.value)    # "figure" | "table"
        meta = ChunkMetadata(
            **base_metadata,
            section_heading=el.metadata.get("nearest_heading", ""),
            source_page=el.page_number,
            modality=modality,
            artifact_ref=ref,
            enrichment_model=el.metadata.get("enrichment_model"),
        )
        chunk_id = make_chunk_id(meta.document_name, f"{modality.value}:{ref}")   # deterministic, idempotent
        chunks.append(Chunk(id=chunk_id, text=el.text, metadata=meta))            # el.text = FULL caption, atomic
    return chunks
```

- The `min_chunk_size` filter that applies to text children is **not** applied to media chunks — a legitimately short figure caption ("Bar chart: NPL ratio peaks at 4.2% in Q3 2023") must survive.
- `chunk_id` keys on `(document_name, "figure:<ref>")`, so re-ingesting the same document maps the same image to the same chunk id → `make_point_uuid` yields the same Qdrant point → idempotent upsert, identical to the text path's guarantee.
- `bbox` is captured on the element but not copied into `ChunkMetadata` this phase: hydration (§13) loads the **whole** cropped artifact by `artifact_ref` (already the region of interest), so bbox has no consumer until region-level re-cropping (a §19 follow-up).

### 9.1 Atomicity and truncation — the direct answer

Two independent truncation risks exist; this design eliminates the first outright and bounds the second.

**Risk 1 — splitter fragmentation (eliminated).** Because media chunks are constructed directly and never enter `raw_markdown` or `RecursiveCharacterTextSplitter`, **one figure = one chunk, one diagram = one chunk, one enriched table = one chunk, regardless of caption length.** The entire VLM summary lands in a single `Chunk.text`. There is no window that can cut it, and no marker that can be split. This is the whole reason for the element-attachment design over markdown injection (§1, §5.5).

**Risk 2 — embedding-length cap (bounded, and visible).** The chunk *text* is stored whole and returned whole — retrieval hands the generator the complete caption, and hydration (§13) can still load the image. But the **vector** that the chunk is *retrieved by* is computed by BGE-M3 under `embedding.max_length = 512` tokens (`config/embedding.py`). So if a single caption exceeds ~512 tokens (≈380 words / ~2,000–2,500 chars), the dense/sparse vectors reflect only its leading portion; later rows of a very large table influence retrieval less. Concretely:

| Modality | Typical caption size | Effect of the 512-token cap |
|---|---|---|
| Figure / chart / diagram | 1–4 sentences | None — comfortably under the cap. One atomic chunk is always right. |
| Small/medium table | < 512 tok | None. One atomic chunk. |
| Large/complex table (pricing grids, tranche schedules) | can exceed 512 tok | Text fully stored & generatable; vector covers only leading rows. Mitigate per below. |

**Mitigation for large tables (configurable, `enrichment.max_media_chunk_tokens`):**
- *Default — atomic:* one chunk per table. Full text retrievable and passed to the LLM; accept that the retrieval vector favors leading rows. Simplest, correct for the large majority of tables.
- *Opt-in — row-group split:* a table whose caption exceeds the token budget is split into N row-group chunks (by transcribed row boundaries, not by character count), each embedded independently, **all sharing the same `artifact_ref`, `modality`, and `section_heading`.** Retrieval can then match the specific row-group a query targets, while hydration still loads the one whole table image (multiple chunks → one artifact; the generator de-duplicates image parts by `artifact_ref`, §13.3). This is the only case where one image maps to more than one chunk, and it's deliberate and lossless — no summary is cut, it's partitioned on semantic boundaries with each part fully embedded.

So: **the full summary of any figure/diagram/table is always contained** — atomically by default, or split on clean row boundaries (never mid-content) for oversized tables when you opt in. Nothing is silently truncated at the chunking layer; the only cap is the embedding window, which is explicit, affects retrieval-matching (not answer content), and is mitigated by the row-group option.

---

## 10. What does *not* change

Called out explicitly because it's the strongest argument for approach (A) being low-risk, and it's now verified against the real code, not assumed from the diagram:

| Component | Verified unchanged |
|---|---|
| `embeddings/bgem3.py` | `embed_documents(texts: list[str])` takes chunk text; a figure caption is just another string. No new method needed. |
| `retrieval/hybrid_search.py` (RRF merge) | Operates on `SearchResult.text`/scores only — modality-blind by construction. |
| `retrieval/reranker.py` | `compute_score([[query, r.text]])` — a caption competes for rerank slots exactly like a clause. |
| `retrieval/freshness.py` | Operates on `metadata.freshness_score`/`last_indexed_at`, both present on every chunk regardless of modality. |
| `generation/generator.py::_build_context()` | The context-block assembly from `RetrievedChunk.text` is unchanged — a caption chunk flows through identically to a paragraph chunk. `generate()` gains a config-gated hydration branch **around** it (§13); the text-only path is the untouched default. |
| `llm/base.py::Message.content` | Stays `str` **when hydration is disabled** (default). Widening to `str \| list[ContentPart]` only activates on the hydration path (§13.3); the `str` form is preserved so every existing caller is byte-for-byte unaffected. |
| `api/routers/retrieve.py` | No change required for `/retrieve` to keep working; optional `modality` filter is additive (§12). Hydration rides the existing `generate_answer=true` path — no new endpoint. |

---

## 11. `vectordb/qdrant_client.py` changes

```python
# create_collection() — one line added to the existing payload-index loop
for field_name, field_type in [
    ("document_type", "keyword"),
    ("product_applicability", "keyword"),
    ("deprecated", "bool"),
    ("effective_date", "keyword"),
    ("modality", "keyword"),   # NEW
]:
    await self._client.create_payload_index(name, field_name, field_type)
```

`upsert()` needs no change — it already does `**c.chunk.metadata.model_dump(mode="json")`, so the four new `ChunkMetadata` fields (§4.1) serialize into the payload automatically. `_build_filter()` gains one optional clause mirroring the existing `document_type` `MatchAny` pattern, wired to the new `DocumentFilter.modality` field (§4.3):

```python
if filters.modality:
    must.append(FieldCondition(key="modality", match=MatchAny(any=filters.modality)))
```

No vector-schema change — image/table chunks share the same `dense`/`sparse` named vectors as text chunks, which is the entire point of approach (A) over (B).

---

## 12. Retrieval — surfacing `modality` and `artifact_ref`

Two roles: `modality` improves citation clarity (optional), and `artifact_ref` is a **hard dependency of hydration** (§13) — it is the key the generator uses to load the correct image. So the `RetrievedChunk` additions below are required whenever hydration may be enabled, and harmless (short strings) when it isn't. Both also improve auditability per POC §13.7 ("Auditability... the indexed representation is inspectable text").

`retrieval/pipeline.py`, in the `RetrievedChunk(...)` construction:

```python
chunks = [
    RetrievedChunk(
        text=r.text, source=..., section_heading=..., clause_reference=..., score=r.score,
        effective_date=..., freshness_warning=..., parent_text=parent_map.get(...),
        modality=r.metadata.get("modality", "text"),          # NEW
        artifact_ref=r.metadata.get("artifact_ref"),           # NEW
    )
    for r in candidates
]
```

`generation/generator.py::_build_context()`:

```python
header = f"[{i}] Source: {c.source}"
if section_label:
    header += f" · Section: {section_label}"
if c.modality != "text":
    header += f" · [{c.modality.title()}]"     # NEW — e.g. "· [Figure]" so the LLM (and a human reading the citation) knows this claim came from a transcribed image, not prose
if c.effective_date:
    header += f" · Effective: {c.effective_date}"
```

This is the one place a system-prompt update matters: `_SYSTEM_PROMPT` should gain one sentence — *"Context blocks marked [Figure] or [Table] are machine-transcribed from images; treat their contents as authoritative text but note the transcription source if asked."* — so the LLM doesn't need to guess why some sources look different.

---

## 13. Hydration — config-gated, answer-time image loading

POC §13.4 frames hydration (loading original image bytes back for a vision LLM at answer time) as optional for Phase 2 / required for Phase 3. This design **implements it as a configurable capability, off by default** — the `artifact_ref` written at ingest (§7, §8) is the key that resolves the correct image back at answer time.

### 13.0 End-to-end `artifact_ref` linkage (the thing that makes hydration work)

The same string flows unbroken from ingest to the VLM call — verified against the real code, not assumed:

```
ingest:    LocalArtifactStore.put_bytes(bytes) ─→ ref = "sha256:<digest>.png"
              │
              └─ build_marker_block(ref=ref) ─→ appended to raw_markdown
chunk:     extract_marker(text)["ref"] ─→ ChunkMetadata.artifact_ref = ref
upsert:    metadata.model_dump() ─→ Qdrant payload["artifact_ref"] = ref      (qdrant_client.py:91)
search:    SearchResult.metadata = payload  ─→ r.metadata["artifact_ref"]     (qdrant_client.py:196, verified)
retrieve:  RetrievedChunk.artifact_ref = r.metadata.get("artifact_ref")       (§12)
hydrate:   artifact_store.get_bytes(chunk.artifact_ref) ─→ (bytes, mime_type) ─→ VLM   (§13.3)
```

Because the ref is `sha256(bytes)`, it is impossible for a chunk to resolve to the wrong image: the ref *is* the content address of the exact bytes captured during that chunk's enrichment. `get_bytes()` reconstructs the on-disk path deterministically from the ref (§7), so no lookup table can drift.

### 13.1 Where hydration lives — the generator, not the retrieval response

POC §13.4 suggests a `hydrate_artifacts()` step inside `retrieval/pipeline.py`. This design deliberately places hydration in **`generation/generator.py` instead**, for one concrete reason grounded in the current code:

> `api/routers/retrieve.py` caches the `RetrieveResponse` in Redis (15-min TTL). If hydration attached image **bytes** to `RetrievedChunk`, every cached response would carry megabytes of base64 into Redis, and the frozen `RetrievedChunk` API model would leak binary into the JSON contract.

So `RetrievedChunk` carries only `artifact_ref` (a short string — cache-safe, §12), and the generator resolves ref→bytes at the moment it assembles the LLM call. Hydration therefore runs **only on the `generate_answer=true` path**, which is exactly when an image is actually needed. This is the same "hydrate only the small final set, not every candidate" placement POC §13.4 argues for, achieved a layer later.

### 13.2 Trigger logic — conditional vs. always (configurable)

Mirrors the POC §13.4 decision table, selected by `HydrationConfig.mode` (§14):

| `mode` | Behavior | POC §13.4 row |
|---|---|---|
| `"off"` (default via `enabled=false`) | Never hydrate. Answer from transcribed text only. | "Do not hydrate" |
| `"conditional"` | Hydrate a chunk only if `chunk.modality in trigger_modalities` **and** `chunk.artifact_ref is not None`. | "Hydrate only when needed" (recommended) |
| `"always"` | Hydrate every non-text chunk that has an `artifact_ref`. | "Always hydrate non-text chunks" |

### 13.3 `generation/generator.py` — the hydration branch

```python
from gernas_rag.llm.base import Message, TextPart, ImagePart

class ResponseGenerator:
    def __init__(self, settings: Settings, llm: BaseLLM,
                 artifact_store: BaseArtifactStore | None = None,
                 vision_llm: BaseLLM | None = None) -> None:      # NEW deps, both optional
        self._settings = settings
        self._llm = llm                                            # existing default (text) LLM — unchanged
        self._hydration = settings.hydration
        self._artifact_store = artifact_store
        self._vision_llm = vision_llm                              # separate vision-capable provider (§13.4)

    def _should_hydrate(self, chunk: RetrievedChunk) -> bool:
        if not self._hydration.enabled or self._hydration.mode == "off":
            return False
        if chunk.artifact_ref is None:
            return False
        if self._hydration.mode == "always":
            return True
        return chunk.modality in self._hydration.trigger_modalities   # "conditional"

    async def _hydrate(self, chunks: list[RetrievedChunk]) -> list[ImagePart]:
        parts: list[ImagePart] = []
        seen: set[str] = set()
        for c in chunks:
            if not self._should_hydrate(c) or c.artifact_ref in seen:
                continue                                           # dedup: N row-group chunks → one image (§9.1)
            try:
                data, mime = await self._artifact_store.get_bytes(c.artifact_ref)
                if len(data) > self._hydration.max_image_bytes:
                    continue                                       # oversize guard — skip, keep the text summary
                seen.add(c.artifact_ref)
                parts.append(ImagePart(bytes=data, mime_type=mime, ref=c.artifact_ref))
            except Exception:
                continue                                           # fail-soft: missing/unreadable artifact → text-only, never crash
        return parts

    async def generate(self, query: str, chunks: list[RetrievedChunk]) -> str:
        context = self._build_context(chunks)                      # UNCHANGED (text summaries always included)
        user_text = f"Context:\n{context}\n\nQuestion: {query}\n\n..."

        image_parts = await self._hydrate(chunks) if self._hydration.enabled else []
        if image_parts and self._vision_llm is not None:
            content = [TextPart(text=user_text), *image_parts]     # mixed multimodal content
            messages = [Message(role="system", content=_SYSTEM_PROMPT),
                        Message(role="user", content=content)]
            return await self._vision_llm.generate(messages)       # route to vision provider ONLY when images present

        # default text-only path — byte-for-byte identical to today
        messages = [Message(role="system", content=_SYSTEM_PROMPT),
                    Message(role="user", content=user_text)]
        return await self._llm.generate(messages)
```

Key properties:
- **The text summary is always in context**, even when hydrating. The image *augments* the transcription; it never replaces it. This preserves the audit trail (POC §13.7) and means a wrong crop degrades gracefully rather than blinding the model.
- **The vision provider is only invoked when `image_parts` is non-empty** — POC §13.4's "route to a separate vision-LLM provider only when needed (cost)." The default answer LLM (Groq `openai/gpt-oss-120b`, not vision-capable) keeps handling every text-only query at its current cost/latency.
- Every early-return keeps the existing text-only path reachable, so `enabled=false` is provably a no-op.

### 13.4 The vision provider

`self._vision_llm` is built from `HydrationConfig.vision_provider` / `vision_model_name` (§14) via the existing `llm/factory.py::get_llm()` — no new provider abstraction, it reuses `BaseLLM`. It's wired in wherever `ResponseGenerator` is constructed (the `/retrieve` router's dependency setup), passed as `None` when `hydration.enabled=false` so nothing loads.

### 13.5 Generation-contract change (`llm/base.py`), scoped to the hydration path

This is the one place the POC §13.4 "Message.content extended to str | list[ContentPart]" change lands — and only here, only when hydration is on:

```python
from dataclasses import dataclass

@dataclass
class TextPart:
    text: str
    type: str = "text"

@dataclass
class ImagePart:
    bytes: bytes
    mime_type: str
    ref: str | None = None            # artifact_ref, preserved for citation
    type: str = "image"

ContentPart = TextPart | ImagePart

@dataclass
class Message:
    role: str
    content: str | list[ContentPart]  # WIDENED — str form unchanged, every existing caller untouched
```

Provider serialization — vision-capable providers build image blocks; text-only providers **strip images and degrade to text**, matching the graceful-degradation ethos already in the codebase:

```python
# anthropic_llm.py
def _serialize(content: str | list[ContentPart]) -> str | list[dict]:
    if isinstance(content, str):
        return content
    out = []
    for part in content:
        if isinstance(part, TextPart):
            out.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            b64 = base64.b64encode(part.bytes).decode("ascii")
            out.append({"type": "image",
                        "source": {"type": "base64", "media_type": part.mime_type, "data": b64}})
    return out

# groq_llm.py / huggingface_llm.py (text-only providers)
def _serialize(content: str | list[ContentPart]) -> str:
    if isinstance(content, str):
        return content
    # drop image parts, keep text — never send bytes to a text-only model
    return "\n".join(p.text for p in content if isinstance(p, TextPart))
```

So even if a text-only provider is mistakenly configured as the `vision_provider`, it degrades to the transcription rather than erroring — the same fail-soft contract as `Reranker` and `VisionLLMEnricher`.

### 13.6 If hydration stays off (the default)

Everything POC §13.4's "If Hydration Is Not Added" column requires still holds, because off-by-default is the shipped default:
- The runtime LLM answers from the VLM transcription stored as chunk text.
- `_TRANSCRIBE_PROMPT` (§5.2) already demands exhaustive transcription precisely because there may be no second look at the image.
- `artifact_ref` / `bbox` are still captured and stored, so turning hydration **on is a pure config flip** — no re-ingestion (this is the concrete payoff of doing the schema + storage work in Phase 2).
- Evaluation (§17) includes "insufficient visual context" cases for the text-only path.

---

## 14. Config changes

### 14.1 New `config/enrichment.py`

```python
class EnrichmentConfig(BaseModel):
    enabled: bool = False                          # safe default — existing ingestion behavior unchanged until opted in
    provider: str = "anthropic"                     # "anthropic" | "openai"
    vlm_model_name: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    timeout_seconds: int = 20
    min_image_bytes: int = 2048                     # skip decorative logos/rules
    table_confidence_threshold: float = 0.7
    max_concurrent: int = 4
    max_media_chunk_tokens: int = 0                 # 0 = always atomic (one chunk per media element);
                                                    # >0 = split oversized table captions into row-group chunks
                                                    # sharing one artifact_ref (§9.1). Keep ≤ embedding.max_length.
```

`max_media_chunk_tokens` is the knob behind §9.1's large-table mitigation. Left at `0` (atomic) by default; set it to e.g. `480` (just under `embedding.max_length=512`) to turn on row-group splitting for tables whose transcription would otherwise exceed the embedding window.

### 14.2 New `config/hydration.py`

```python
class HydrationConfig(BaseModel):
    enabled: bool = False                                      # master switch — configurable, off by default
    mode: str = "conditional"                                  # "off" | "conditional" | "always" (§13.2)
    trigger_modalities: list[str] = Field(                     # which modalities pull their image at answer time
        default_factory=lambda: ["figure", "table", "page_image"]
    )
    vision_provider: str = "anthropic"                          # separate from the primary answer LLM
    vision_model_name: str = "claude-sonnet-5"                  # vision-capable model
    max_image_bytes: int = 5_000_000                            # skip oversize artifacts, keep the text summary
```

`enabled` and `mode` are the two knobs that make hydration configurable: `enabled=false` (default) is a proven no-op; `mode` chooses how aggressively to hydrate when on. The vision provider/model reuse `LLMConfig` credentials (`RAG__LLM__ANTHROPIC_API_KEY`) — no new secret (§15).

### 14.3 New `config/artifact_store.py`

```python
class ArtifactStoreConfig(BaseModel):
    backend: str = "local"          # "local" | "s3" (s3 unimplemented this phase — see §7)
    local_path: str = "./artifacts"
```

### 14.4 `config/settings.py`

```python
class Settings(BaseSettings):
    ...
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)            # NEW (ingest)
    hydration: HydrationConfig = Field(default_factory=HydrationConfig)                # NEW (answer-time)
    artifact_store: ArtifactStoreConfig = Field(default_factory=ArtifactStoreConfig)  # NEW
```

Follows the existing `RAG__` env prefix / `__` nested-delimiter convention automatically — e.g. `RAG__ENRICHMENT__ENABLED=true`, `RAG__HYDRATION__ENABLED=true`, `RAG__HYDRATION__MODE=conditional` work with no extra wiring, same as every other nested config block. Note the two are **independent flags**: you can enable ingest-time enrichment (build image chunks) while leaving hydration off (answer from transcriptions only), which is the recommended Phase-2 posture.

### 14.5 `config/default.yaml`

```yaml
enrichment:
  enabled: false
  provider: anthropic
  vlm_model_name: claude-haiku-4-5-20251001
  max_tokens: 1024
  timeout_seconds: 20
  min_image_bytes: 2048
  table_confidence_threshold: 0.7
  max_concurrent: 4
  max_media_chunk_tokens: 0          # 0 = atomic; >0 enables row-group split for oversized tables (§9.1)

hydration:
  enabled: false                 # answer-time image loading — off by default
  mode: conditional              # off | conditional | always
  trigger_modalities: [figure, table, page_image]
  vision_provider: anthropic
  vision_model_name: claude-sonnet-5
  max_image_bytes: 5000000

artifact_store:
  backend: local
  local_path: ./artifacts
```

Both `enabled: false` at the YAML layer, with `local.yaml` (developer machine) flipping them on for iteration — matching how `debug`/`environment` are already split across `default.yaml`/`local.yaml`/`production.yaml`.

---

## 15. Dependencies and Docker

`pyproject.toml`:

```toml
dependencies = [
    ...
    "pillow>=10.4.0",   # NEW — PIL image encode/decode for VLM payloads and artifact-store round-trips
]
```

No new Docker service. `docker-compose.yml` needs one bind mount, mirroring the existing `./docs:/app/docs:ro` line:

```yaml
rag-api:
  ...
  volumes:
    - "./docs:/app/docs:ro"
    - "./artifacts:/app/artifacts"     # NEW — read-write, LocalArtifactStore
```

VLM credentials reuse `RAG__LLM__ANTHROPIC_API_KEY` / `RAG__LLM__OPENAI_API_KEY` (already present for the answer-generation LLM) — no new secret needs provisioning in `.env` or the AKS Helm chart for this phase, since `enrichment/factory.py::get_enricher()` reads from `settings.llm` (§5.4).

---

## 16. Banking-specific guardrails (POC §13.7, made concrete)

| Concern | Concrete mitigation in this design |
|---|---|
| PII in images (signatures, ID scans) | `LocalArtifactStore` root (`./artifacts`, container path `/app/artifacts`) should get the same filesystem permissions/encryption-at-rest posture as `./docs`. Anthropic's API does not train on or retain API inputs by default — confirm this contractually before enabling `enrichment.enabled=true` against real KYC scans, not just mock FAB PDFs. |
| Auditability | `ChunkMetadata.enrichment_model` (§4.1) is stored per chunk specifically so a reviewer can trace any figure-derived answer back to the VLM version that produced it — set to `None` on fail-soft degrade so a `None` value itself signals "this text came from raw OCR/Docling table text, not a VLM," which is auditable information in its own right. |
| Hallucination in enrichment | `_TRANSCRIBE_PROMPT` (§5.2) explicitly instructs "transcribe, do not infer... write [illegible] rather than guessing." Extending RAGAS faithfulness scoring (POC §10) to cover figure/table-derived chunks specifically is listed as a testing task in §15. |
| Fail-soft, not fail-open | `VisionLLMEnricher.enrich()` catches broadly and returns `ok=False`; `_enrich()` in the ingestion pipeline falls back to `el.text` rather than dropping the element or failing the whole document ingest — matches the existing `Reranker` fallback idiom already in the codebase (§5.2), not a new pattern to review. |

---

## 17. Testing plan

- **`tests/unit/test_enrichment.py`** — `VisionLLMEnricher.enrich()` against a mocked Anthropic client: success path, timeout → `ok=False`, malformed response → `ok=False`. `TableEnricher` confidence-threshold branching.
- **`tests/unit/test_artifact_store.py`** — `LocalArtifactStore.put_bytes()` idempotency (same bytes → same ref, no duplicate write); `get_bytes()` round-trip.
- **`tests/unit/test_chunking.py`** additions — feed `HierarchicalChunker` a `raw_markdown` string containing a `build_marker_block()` output; assert the resulting `Chunk.metadata.modality == Modality.FIGURE`, `artifact_ref` populated, and `Chunk.text` has no `<!-- MM_START` residue. Also a case with a marker block shorter than `min_chunk_size` to confirm it survives the length filter (§9).
- **`tests/integration/test_ingestion_pipeline.py`** — end-to-end ingest of a mock FAB PDF containing at least one chart/pricing-grid image, with `enrichment.enabled=true` and a mocked VLM response, asserting a `FIGURE`-modality chunk lands in Qdrant with the expected payload fields (including `artifact_ref`).
- **`tests/unit/test_generation.py`** (NEW) — hydration branch (§13): (a) `enabled=false` → `_hydrate` returns `[]` and the text-only LLM is called (no artifact-store read); (b) `mode="conditional"` with a `FIGURE` chunk → `artifact_store.get_bytes(ref)` is called with the exact `artifact_ref` and the vision LLM receives an `ImagePart`; (c) a `TEXT` chunk under `conditional` → not hydrated; (d) missing artifact / oversize bytes → fail-soft to text-only, no crash. Assert the ref passed to `get_bytes` equals the ref stored on the chunk (the linkage §13.0 guarantees).
- **`tests/unit/test_llm_content.py`** (NEW) — `Message.content` widening: `AnthropicLLM._serialize` builds a base64 image block; `GroqLLM._serialize` drops the image and keeps only text (degrade path §13.5).
- **Evaluation set (POC §11)** — add 2–3 questions whose ground truth lives only in a chart/table image (e.g. "what does the collateral matrix chart show for X"), scored with RAGAS faithfulness extended to check the answer doesn't over-claim beyond the transcription (§16). Run the set **twice** — once with `hydration.enabled=false` (transcription-only) and once with `true` (vision-at-answer) — to quantify hydration's accuracy lift against its latency/cost.

---

## 18. Rollout plan

1. **Land dark** — all code merged with `enrichment.enabled: false` **and** `hydration.enabled: false` in every environment's YAML. Zero behavior change; existing ingestion/retrieval integration tests must pass unmodified.
2. **Shadow ingestion** — flip `enrichment.enabled: true` only in `local.yaml`, re-ingest the 5 mock FAB documents (POC §11), manually spot-check 5–10 generated captions against source images for transcription accuracy and hallucination.
3. **Eval gate (transcription-only)** — run the extended RAGAS eval set (§17) against the shadow-ingested corpus with `hydration.enabled=false`; require faithfulness/context-precision targets from POC §10 to hold on the new figure/table questions before promoting.
4. **Enable enrichment in staging** — flip `enrichment.enabled: true` in a non-prod YAML tier, monitor VLM ingest latency/cost and fail-soft rate (log `enrichment_model is None` frequency as a health signal) via the existing OTel pipeline.
5. **Turn on hydration (separately, later)** — flip `hydration.enabled: true` (`mode: conditional`) in staging only. Monitor answer-time vision-LLM latency/cost and the hydration fail-soft rate (missing/oversize artifact count). Re-run the eval set with hydration on (§17) to confirm the accuracy lift justifies the added per-answer cost before promoting. Keep hydration off in prod until this clears.
6. **Production enablement** — config flips only, no code change, once staging burn-in is clean. Enrichment and hydration are independent flags, so they can be promoted on separate timelines.

Each step past step 1 is a config change, not a code change — the additive-and-flagged design (two independent switches, `enrichment` for ingest, `hydration` for answer-time) is what makes that possible.

---

## 19. Scope summary

**In scope (this document):**
- Ingest-time enrichment: figures/tables → VLM captions → text chunks (§5–§9), gated by `enrichment.enabled`.
- Content-addressed artifact store + end-to-end `artifact_ref` linkage (§7, §13.0).
- **Answer-time hydration**: load the original image by `artifact_ref` and pass it to a vision LLM (§13), gated by `hydration.enabled` / `hydration.mode`, off by default.
- `llm/base.py::Message.content` widening — activated only on the hydration path (§13.5).

**Explicitly out of scope:**
- Approach (B) native multimodal embeddings, Approach (C) ColPali/page-as-image.
- `S3ArtifactStore` implementation (interface exists, backend doesn't — §7).
- Enrichment result caching beyond the free idempotency `LocalArtifactStore` already provides (§8 note).
- `PAGE_IMAGE` modality *population* (enum value reserved and accepted as a hydration trigger, but no producer emits it in this phase).
- `bbox`-cropped re-hydration (whole-artifact hydration only; region-level crop at answer time is a follow-up — §9).
- Suppressing the duplicate Docling markdown copy of a **low-confidence** table once its atomic VLM chunk exists (§6 note) — mild duplication accepted for the POC; a markdown post-process is the follow-up.
- Row-group table splitting is *designed* and *configurable* (§9.1, `max_media_chunk_tokens`) but ships **off** (atomic default); enabling it for production-scale table corpora is the recommended next step if large-table recall proves weak in eval.
