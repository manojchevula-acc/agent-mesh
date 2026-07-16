"""Build & upsert the few-shot EXAMPLE vector index (offline) — Pattern Retriever.

Embeds each APPROVED example's question (from the metadata DB) with the configured
embedding model and upserts the vectors into the configured vector backend
(memory | faiss | qdrant) under the SEPARATE ``examples_qdrant_collection`` — the same
code path the agent uses lazily on its first dynamic request, run here explicitly so you
can pre-populate the index and verify what got stored.

Run:  uv run python scripts/build_example_index.py            # build (idempotent)
      uv run python scripts/build_example_index.py --force    # rebuild even if populated
                                                              # (run after EDITING the seed
                                                              #  set or changing EMBEDDING_MODEL)

Prereq: seed the examples first with  scripts/seed_examples.py  (writes the metadata DB).
Reads .env for EMBEDDING_* and VECTOR_* / QDRANT_* / EXAMPLES_QDRANT_* settings.

IMPORTANT (local Qdrant): QDRANT_PATH mode holds an exclusive file lock, so STOP the API
server before running this — one process at a time may open ./qdrant_data. The schema and
example collections share that one client, so this also can't run while the schema-index
builder holds the path.
"""

from __future__ import annotations

import argparse

from sql_agent.config import settings
from sql_agent.memory.example_index import example_doc_text
from sql_agent.memory.examples import all_approved_examples
from sql_agent.semantic_layer.catalog import glossary_expand
from sql_agent.semantic_layer.embeddings import get_backend
from sql_agent.semantic_layer.vector_index import get_example_vector_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build & upsert the example vector index.")
    parser.add_argument("--force", action="store_true",
                        help="rebuild even if the store is already populated "
                             "(use after editing the seed set or EMBEDDING_MODEL)")
    args = parser.parse_args()

    if settings.embedding_backend == "none":
        raise SystemExit("EMBEDDING_BACKEND=none — nothing to embed. Set it to 'local' or 'azure'.")

    rows = all_approved_examples()
    if not rows:
        raise SystemExit("No approved examples found — run scripts/seed_examples.py first "
                         "(and check EXAMPLES_ENABLED / AGENT_DB_DSN).")

    names = [r["question"] for r in rows]
    # Payload: tier + tags + metadata for reference/inspection (not used by similarity
    # search — the ranker reads metadata from the metadata DB, not the vector payload).
    payloads = {r["question"]: {"tier": r.get("tier", ""), "tags": r.get("tags", ""),
                                "metadata": r.get("metadata") or ""}
                for r in rows}

    print(f"Examples to index : {len(names)}")
    print(f"Embedding model   : {settings.embedding_model} (backend={settings.embedding_backend})")
    backend = get_backend()
    if backend is None:
        raise SystemExit("No embedding backend resolved — check EMBEDDING_BACKEND.")

    print("Embedding example questions + SQL query-logic shape ...")
    # Same document text the runtime retriever embeds (question + query-logic shape,
    # see example_index.example_doc_text) — keeps an offline-built index from drifting
    # from what sql_agent/memory/example_index.py._build() would produce.
    vectors = backend.embed([example_doc_text(r) for r in rows])

    print(f"Vector backend    : {settings.vector_backend} "
          f"(collection={settings.examples_qdrant_collection}, "
          f"qdrant path={settings.qdrant_path or '-'}, url={settings.qdrant_url or '-'})")
    print(f"Upserting vectors ...{' (force)' if args.force else ''}")
    index = get_example_vector_index()
    try:
        index.build(names, vectors, payloads, force=args.force)

        # Verify: a sample query should return sensible candidates.
        probe = "how much capital is tied up in our riskiest deals"
        qv = backend.embed_query(glossary_expand(probe),
                                  prefix=settings.examples_embedding_query_prefix)
        hits = index.search(qv, 5)
        print("\nUpsert complete. Sample search:")
        print(f"  query: {probe!r}")
        for name, score in hits:
            print(f"    {score:6.3f}  {name}")
    finally:
        index.close()  # release the local-mode file lock cleanly (no shutdown noise)


if __name__ == "__main__":
    main()
