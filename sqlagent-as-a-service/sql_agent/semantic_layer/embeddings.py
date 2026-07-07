"""Swappable embedding backend for scoped schema retrieval (Component B).

local  -> sentence-transformers, default BAAI/bge-base-en-v1.5 (768-dim). Chosen over
          MiniLM for materially stronger short-doc / asymmetric (query->table) retrieval,
          while staying small and on-prem (keeps the question text from egressing).
azure  -> Azure OpenAI embeddings (e.g. text-embedding-3-small) if the org is
          standardised on Azure.
none   -> no dense backend; the selector degrades to BM25-only.

Heavy imports (sentence-transformers / numpy) are lazy so the package imports cleanly
even when the optional `retrieval` extra is not installed and the feature flag is off.
Schema vectors are built once and cached by the selector; the question is embedded per
request. bge/e5 models want a query-side instruction prefix — see ``embed_query``.
"""

from __future__ import annotations

from functools import lru_cache

from sql_agent.config import settings


class EmbeddingBackend:
    def embed(self, texts: list[str]):
        """Embed documents (table summaries). Returns a 2-D array-like."""
        raise NotImplementedError

    def embed_query(self, text: str):
        """Embed a single query. Default: same as a document (OpenAI/Azure style)."""
        return self.embed([text])[0]


class LocalEmbeddings(EmbeddingBackend):
    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)

    def embed(self, texts):
        import numpy as np

        return np.asarray(self._model.encode(texts, normalize_embeddings=True))

    def embed_query(self, text):
        # bge/e5: prepend the retrieval instruction to the QUERY only, not the documents.
        return self.embed([settings.embedding_query_prefix + text])[0]


class AzureEmbeddings(EmbeddingBackend):
    def __init__(self, model: str) -> None:
        from langchain_openai import AzureOpenAIEmbeddings

        self._model = AzureOpenAIEmbeddings(
            azure_deployment=model,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    def embed(self, texts):
        import numpy as np

        return np.asarray(self._model.embed_documents(list(texts)))


@lru_cache(maxsize=1)
def get_backend() -> EmbeddingBackend | None:
    """Resolve the configured dense backend, or None when embeddings are disabled.
    Cached so the model is loaded once per process."""
    backend = settings.embedding_backend.strip().lower()
    if backend == "local":
        return LocalEmbeddings(settings.embedding_model)
    if backend == "azure":
        return AzureEmbeddings(settings.embedding_model)
    return None  # "none" -> BM25-only retrieval
