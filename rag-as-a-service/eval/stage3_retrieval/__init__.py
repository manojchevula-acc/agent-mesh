"""Stage 3 — are the right chunks retrieved, and in the right order?

Scored against graded relevance judgments keyed by ``chunk_id``. Identity
matters here: ``clause_reference`` is derived from chunk text (and for media
chunks from transcribed caption text), so matching on it both misses genuine
hits and counts wrong ones. ``chunk_id`` is deterministic and unique.

The runner reproduces each pipeline stage separately — dense, sparse, RRF,
rerank, freshness — so a drop can be attributed to the component that caused it,
and verifies its final ordering against the real ``RetrievalPipeline`` so the
ablation can never silently drift away from what production does.
"""
