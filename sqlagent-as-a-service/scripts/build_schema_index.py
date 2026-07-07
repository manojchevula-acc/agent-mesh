"""Build & upsert the schema vector index (offline).

Embeds each governed table's document (name + grain + column names + descriptions +
enum values) with the configured embedding model and upserts the vectors into the
configured vector backend (memory | faiss | qdrant) — the same code path the agent uses
lazily on its first dynamic request, run here explicitly so you can:
  * pre-populate the index (so the first API request isn't slow), and
  * verify what got stored.

Each table's declared JOIN neighbours are attached as payload for reference. Joins are
NOT used for similarity search — they are resolved deterministically from schema.yaml at
query time (relationship_graph / resolve_joins); the payload is informational only.

Run:  uv run python scripts/build_schema_index.py            # build (idempotent)
      uv run python scripts/build_schema_index.py --force    # rebuild even if populated
Reads .env for EMBEDDING_* and VECTOR_* / QDRANT_* settings.

IMPORTANT (local Qdrant): QDRANT_PATH mode holds an exclusive file lock, so STOP the API
server before running this — one process at a time may open ./qdrant_data.
"""

from __future__ import annotations

import argparse

from sql_agent.config import settings
from sql_agent.semantic_layer.embeddings import get_backend
from sql_agent.semantic_layer.loader import relationship_graph
from sql_agent.semantic_layer.selector import _table_docs
from sql_agent.semantic_layer.vector_index import get_vector_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build & upsert the schema vector index.")
    parser.add_argument("--force", action="store_true",
                        help="rebuild even if the store is already populated "
                             "(use after changing EMBEDDING_MODEL — the dim may change)")
    args = parser.parse_args()

    if settings.embedding_backend == "none":
        raise SystemExit("EMBEDDING_BACKEND=none — nothing to embed. Set it to 'local' or 'azure'.")

    docs = _table_docs()                 # {table_name: rich document string}
    names = list(docs)
    graph = relationship_graph()         # authoritative join graph (from schema.yaml)
    # Payload: the table name (used by search) + its declared join neighbours (reference).
    payloads = {n: {"joins": sorted(graph.get(n, {}).keys())} for n in names}

    print(f"Tables to index : {len(names)}")
    print(f"Embedding model : {settings.embedding_model} (backend={settings.embedding_backend})")
    backend = get_backend()
    if backend is None:
        raise SystemExit("No embedding backend resolved — check EMBEDDING_BACKEND.")

    print("Embedding table documents ...")
    vectors = backend.embed([docs[n] for n in names])

    print(f"Vector backend  : {settings.vector_backend} "
          f"(qdrant path={settings.qdrant_path or '-'}, url={settings.qdrant_url or '-'})")
    print(f"Upserting vectors ...{' (force)' if args.force else ''}")
    index = get_vector_index()
    try:
        index.build(names, vectors, payloads, force=args.force)

        # Verify: a sample query should return sensible candidates.
        probe = "average won deal margin broken down by customer segment"
        qv = backend.embed_query(probe)
        hits = index.search(qv, 5)
        print("\nUpsert complete. Sample search:")
        print(f"  query: {probe!r}")
        for name, score in hits:
            print(f"    {score:6.3f}  {name}   joins-> {payloads[name]['joins']}")
    finally:
        index.close()  # release the local-mode file lock cleanly (no shutdown noise)


if __name__ == "__main__":
    main()
