"""Shared building blocks for every evaluation stage.

Nothing in ``core`` talks to the RAG pipeline except :mod:`eval.core.corpus`;
the metric implementations are pure functions over plain data so they can be
unit-tested without a vector DB, a model download or an API key.
"""
