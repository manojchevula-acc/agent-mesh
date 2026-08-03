"""Stage 1 — did the extractor find every figure, table and heading?

This stage bounds every later stage from above: a figure Docling never detects
can never be captioned, indexed, retrieved or cited, and no downstream metric
will ever tell you it was missing. It is the only stage that reads the source
PDFs rather than the index.
"""
