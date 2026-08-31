"""Build & persist the metadata Knowledge Graph (offline).

Reads information_schema + schema.yaml + data_dictionary + business_glossary, writes the
JSON artifact, optionally upserts it into Neo4j, and rebuilds the :Term/:Scenario vector index.

Run:  uv run python scripts/build_metadata_kg.py            # build + write
      uv run python scripts/build_metadata_kg.py --check    # drift report only, no write
      uv run python scripts/build_metadata_kg.py --force    # also rebuild the node index

--check is the CI / migration hook: it exits 1 on blocking drift (a declared table or
column that no longer exists), so a migration fails the pipeline instead of quietly
de-grounding the agent (design §8.4).

IMPORTANT (local Qdrant): QDRANT_PATH mode holds an exclusive file lock, so STOP the API
server before running with --force — one process at a time may open ./qdrant_data.
"""

from __future__ import annotations

import argparse
import sys

from sql_agent.config import settings
from sql_agent.kg.builder import build_metadata_graph, write_artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the metadata Knowledge Graph.")
    parser.add_argument("--check", action="store_true",
                        help="report drift and exit non-zero if blocking; do not write")
    parser.add_argument("--force", action="store_true",
                        help="also rebuild the :Term/:Scenario vector index (needed after editing "
                             "business_glossary.yaml, purpose/search_terms, or EMBEDDING_MODEL)")
    args = parser.parse_args()

    graph, drift = build_metadata_graph()

    print(f"Source database : {graph.source_database}")
    print(f"Tables          : {len(graph.tables)}")
    print(f"Columns         : {len(graph.columns)}")
    print(f"Terms           : {len(graph.terms)}  "
          f"({sum(1 for t in graph.terms.values() if t.definition)} with definitions)")
    print(f"Edges           : {len(graph.foreign_keys)} "
          f"({len(graph.active_edges())} active, "
          f"{len(graph.foreign_keys) - len(graph.active_edges())} proposed)")
    print(f"Fingerprint     : {graph.fingerprint}")
    print(f"\n--- Drift ---\n{drift.render()}")

    if args.check:
        if drift.has_blocking_drift:
            print("\nBLOCKING DRIFT — the semantic layer references objects that no longer "
                  "exist. Fix schema.yaml (or revert the migration) before rebuilding.",
                  file=sys.stderr)
            raise SystemExit(1)
        print("\nNo blocking drift.")
        return

    path = write_artifact(graph)
    print(f"\nArtifact written: {path}")

    if settings.kg_backend.strip().lower() == "neo4j":
        from sql_agent.kg.client import get_kg_client, reset_kg_client

        reset_kg_client()
        client = get_kg_client()          # load() upserts via MERGE
        print(f"Neo4j upsert    : {'ok' if client is not None else 'FAILED (see logs)'}")

    if args.force:
        from sql_agent.kg.retrieval import build_node_index

        count = build_node_index(force=True)
        print(f"Node index      : {count} vector(s) "
              f"({settings.embedding_model}, backend={settings.vector_backend})")


if __name__ == "__main__":
    main()
