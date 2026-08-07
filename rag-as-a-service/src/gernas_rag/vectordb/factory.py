"""Vector DB factory."""

from ..config.vectordb import VectorDBConfig, VectorDBProvider
from .base import BaseVectorDB


def get_vectordb(config: VectorDBConfig) -> BaseVectorDB:
    match config.provider:
        case VectorDBProvider.QDRANT:
            from .qdrant_client import QdrantVectorDB

            try:
                return QdrantVectorDB(config)
            except RuntimeError as exc:
                if "already accessed by another instance" not in str(exc):
                    raise
                # Embedded Qdrant takes an exclusive lock on its storage folder.
                # The stock traceback is 40 lines of portalocker internals that
                # bury the one thing the operator needs to know.
                raise RuntimeError(
                    f"Qdrant embedded storage '{config.qdrant_path}' is locked by "
                    "another process.\n"
                    "  Only ONE process may hold it at a time — the API server "
                    "and any script are mutually exclusive.\n"
                    "  Fix: stop the other process (uvicorn, or another script), "
                    "then retry.\n"
                    "  Or run a Qdrant server and set RAG__VECTORDB__QDRANT_URL "
                    "instead of QDRANT_PATH for concurrent access."
                ) from exc
        case VectorDBProvider.MILVUS:
            from .milvus_client import MilvusVectorDB

            return MilvusVectorDB(config)
        case VectorDBProvider.CHROMADB:
            from .chromadb_client import ChromaVectorDB

            return ChromaVectorDB(config)
        case _:
            raise ValueError(f"Unsupported vector DB provider: {config.provider}")
