"""Swappable vector index for dense schema retrieval (Component B — storage layer).

Separates WHERE the schema vectors are stored/searched from HOW documents are embedded
(embeddings.py). Backends, selected by ``settings.vector_backend``:

  memory - brute-force NumPy cosine. Zero infra; default for dev/CI and POC scale
           (a handful of tables => a few KB of vectors; a vector DB would be overkill).
  faiss  - flat inner-product index; optional file persistence; no server.
  qdrant - reuse an existing Qdrant service; native payload filtering (e.g. by domain),
           which lets Stage-0 domain routing be pushed into the search itself.

At client scale the data-pipeline builds/persists the index offline and the agent only
searches it; here it is built once per process from the semantic layer. Only the
embedding VECTOR is ever sent to a remote store — never the raw question text.

Heavy imports (numpy / faiss / qdrant_client) are lazy so this module imports cleanly
without the optional `retrieval` extra when the feature flag is off.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from sql_agent.config import settings
from sql_agent.logging_config import get_logger

log = get_logger("vector_index")


@runtime_checkable
class VectorIndex(Protocol):
    def build(self, names: list[str], vectors, payloads: dict | None = None,
              force: bool = False) -> None:
        """Index the document vectors (aligned to ``names``); ``payloads`` is optional
        per-name metadata; ``force`` re-upserts even if the store is already populated."""

    def search(self, query_vector, k: int, where: dict | None = None
               ) -> list[tuple[str, float]]:
        """Return the top-k (name, score) by cosine similarity, honouring ``where`` when
        the backend supports payload filtering (memory + Qdrant; ignored by faiss, whose
        flat index has no payloads — callers needing a filter there must post-filter).

        ``where`` conditions: a scalar value means equality
        (``{"domain": "pricing"}``); ``{"any": [...]}`` means the payload field (itself
        a list) must INTERSECT the given values —
        ``{"tables": {"any": ["margin_analysis"]}}`` matches any point whose ``tables``
        payload contains ``margin_analysis``."""

    def close(self) -> None:
        """Release any underlying resources (no-op for in-process backends)."""


def _match(payload: dict, where: dict) -> bool:
    """In-process `where` evaluation (memory backend; mirrors QdrantIndex.search).
    Scalar condition => equality; {"any": [...]} => the payload field (a list) must
    intersect the given values."""
    for key, cond in where.items():
        got = payload.get(key)
        if isinstance(cond, dict) and "any" in cond:
            wanted = set(cond["any"] or [])
            have = set(got) if isinstance(got, (list, tuple, set)) else {got}
            if not (wanted & have):
                return False
        elif got != cond:
            return False
    return True


class MemoryIndex:
    """Brute-force cosine over an in-RAM matrix. Vectors are assumed L2-normalised by the
    embedding backend, so a dot product is cosine similarity."""

    def __init__(self) -> None:
        self._names: list[str] = []
        self._vectors = None
        self._payloads: dict = {}

    def build(self, names, vectors, payloads=None, force=False) -> None:
        import numpy as np

        self._names = list(names)
        self._vectors = np.asarray(vectors)
        self._payloads = payloads or {}

    def search(self, query_vector, k, where=None):
        import numpy as np

        scores = self._vectors @ np.asarray(query_vector)
        out: list[tuple[str, float]] = []
        for i in scores.argsort()[::-1]:
            name = self._names[i]
            if where and self._payloads and not _match(self._payloads.get(name, {}), where):
                continue
            out.append((name, float(scores[i])))
            if len(out) >= k:
                break
        return out


class FaissIndex:
    """Flat inner-product FAISS index. Optional file persistence (index + names sidecar).
    Payload filtering is not native to a flat index, so ``where`` is ignored."""

    def __init__(self, path: str = "") -> None:
        self._path = path
        self._index = None
        self._names: list[str] = []

    def build(self, names, vectors, payloads=None, force=False) -> None:
        import faiss
        import numpy as np

        self._names = list(names)
        vecs = np.asarray(vectors, dtype="float32")
        self._index = faiss.IndexFlatIP(vecs.shape[1])  # normalised vectors -> cosine
        self._index.add(vecs)
        if self._path:
            faiss.write_index(self._index, self._path)
            import json
            with open(self._path + ".names.json", "w", encoding="utf-8") as fh:
                json.dump(self._names, fh)

    def search(self, query_vector, k, where=None):
        import numpy as np

        q = np.asarray([query_vector], dtype="float32")
        scores, idx = self._index.search(q, min(k, len(self._names)))
        return [
            (self._names[i], float(s))
            for s, i in zip(scores[0], idx[0])
            if i != -1
        ]


class QdrantIndex:
    """Vectors in Qdrant. Supports payload filtering (e.g. by domain) so Stage-0 routing
    can be pushed into the query. Build is idempotent: it skips the upsert when the
    collection already holds the current vectors (i.e. the pipeline, or a prior process,
    populated it).

    Connection precedence (no Docker required):
      url  -> a running Qdrant server (shared / production);
      path -> LOCAL on-disk mode: Qdrant's engine runs IN-PROCESS, durable, no server;
      else -> in-memory (lost on restart).
    Local/in-memory mode holds a file lock, so run a single process (one uvicorn worker).
    """

    def __init__(self, url: str, path: str, api_key: str, collection: str) -> None:
        # A SHARED client per connection (url/path): Qdrant local/on-disk mode file-locks
        # per path, so the schema and examples collections MUST reuse one client in the
        # same process. _qdrant_client caches it.
        self._client = _qdrant_client(url, path, api_key)
        self._collection = collection
        self._names: list[str] = []

    def build(self, names, vectors, payloads=None, force=False) -> None:
        from qdrant_client import models
        import numpy as np

        self._names = list(names)
        vecs = np.asarray(vectors)
        dim = int(vecs.shape[1])

        exists = self._client.collection_exists(self._collection)
        if exists and not force:
            count = self._client.count(self._collection).count
            if count == len(names):
                log.info("Qdrant collection '%s' already populated (%d) — skip upsert "
                         "(pass force=True to rebuild)", self._collection, count)
                return
        self._client.recreate_collection(
            self._collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        payloads = payloads or {}
        self._client.upsert(
            self._collection,
            points=[
                models.PointStruct(id=i, vector=vecs[i].tolist(),
                                   payload={"name": n, **payloads.get(n, {})})
                for i, n in enumerate(names)
            ],
        )
        # Index the list-payload fields used by pre-filtered retrieval ({"any": ...}
        # conditions) so the filter stays cheap as the collection grows. Best-effort:
        # an index on a field no point carries is harmless, and failure to create one
        # only costs filter speed, never correctness.
        for field in ("tables", "intent"):
            if any(field in p for p in payloads.values()):
                try:
                    self._client.create_payload_index(
                        self._collection, field_name=field,
                        field_schema=models.PayloadSchemaType.KEYWORD)
                except Exception as exc:  # noqa: BLE001 — speed optimisation only
                    log.warning("payload index on '%s' failed | %s", field, exc)
        log.info("Qdrant upserted %d schema vectors into '%s'", len(names), self._collection)

    def search(self, query_vector, k, where=None):
        from qdrant_client import models
        import numpy as np

        qfilter = None
        if where:
            conditions = []
            for key, cond in where.items():
                if isinstance(cond, dict) and "any" in cond:
                    # {"any": [...]}: payload list field must intersect the values —
                    # MatchAny on a list payload is Qdrant's native any-of semantics.
                    conditions.append(models.FieldCondition(
                        key=key, match=models.MatchAny(any=list(cond["any"] or []))))
                else:
                    conditions.append(models.FieldCondition(
                        key=key, match=models.MatchValue(value=cond)))
            qfilter = models.Filter(must=conditions)
        # query_points is the current API (replaced the deprecated .search()).
        result = self._client.query_points(
            self._collection,
            query=np.asarray(query_vector).tolist(),
            limit=k,
            query_filter=qfilter,
            with_payload=True,
        )
        return [(p.payload["name"], float(p.score)) for p in result.points]

    def close(self) -> None:
        """Release the client (and, in local mode, the file lock) deterministically —
        avoids the noisy __del__-at-interpreter-shutdown traceback on Windows. Because the
        client is SHARED across collections, this also evicts the cached client so a fresh
        one is created next time (safe: close() is only called by the offline build
        scripts, which use one index at a time)."""
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            self._client = None
            _qdrant_client.cache_clear()


@lru_cache(maxsize=4)
def _qdrant_client(url: str, path: str, api_key: str):
    """One QdrantClient per connection (url/path), shared across collections.

    Qdrant local (``path``) and in-memory modes hold an exclusive lock, so a single
    process must reuse ONE client for every collection it touches (schema + examples).
    """
    from qdrant_client import QdrantClient

    if url:
        return QdrantClient(url=url, api_key=api_key or None)
    if path:
        return QdrantClient(path=path)              # local, on-disk, no server
    return QdrantClient(location=":memory:")          # local, in-RAM


def _make_index(collection: str, index_path: str) -> VectorIndex:
    """Build the configured backend for a specific collection / faiss path."""
    backend = settings.vector_backend.strip().lower()
    if backend == "faiss":
        return FaissIndex(index_path)
    if backend == "qdrant":
        return QdrantIndex(settings.qdrant_url, settings.qdrant_path,
                           settings.qdrant_api_key, collection)
    return MemoryIndex()


@lru_cache(maxsize=1)
def get_vector_index() -> VectorIndex:
    """Resolve the configured vector index backend for the SCHEMA (one per process)."""
    return _make_index(settings.qdrant_collection, settings.vector_index_path)


@lru_cache(maxsize=1)
def get_example_vector_index() -> VectorIndex:
    """Resolve the vector index backend for the few-shot EXAMPLE vectors (Pattern
    Retriever). Same backend/connection as the schema index but a SEPARATE collection."""
    return _make_index(settings.examples_qdrant_collection,
                       settings.examples_vector_index_path)


@lru_cache(maxsize=1)
def get_kg_node_index() -> VectorIndex:
    """Resolve the vector index backend for the metadata-KG :Term and :Scenario vectors.

    Third collection on the SAME backend and, for Qdrant, the same shared client — the KG
    reuses the existing embedding/vector stack wholesale rather than standing up its own.
    ~44 vectors: 23 business terms plus one "what this object is FOR" scenario document per
    table. There is deliberately NO :Column collection — measured at +0 recall on top of
    table-level retrieval, because selector._table_docs already aggregates the same column
    descriptions. See docs/KG_METADATA_LAYER_DESIGN.md section 8."""
    return _make_index(settings.kg_node_collection, settings.kg_node_index_path)
