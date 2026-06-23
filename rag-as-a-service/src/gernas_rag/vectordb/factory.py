"""Vector DB factory."""

from ..config.vectordb import VectorDBConfig, VectorDBProvider
from .base import BaseVectorDB


def get_vectordb(config: VectorDBConfig) -> BaseVectorDB:
    match config.provider:
        case VectorDBProvider.QDRANT:
            from .qdrant_client import QdrantVectorDB

            return QdrantVectorDB(config)
        case VectorDBProvider.MILVUS:
            from .milvus_client import MilvusVectorDB

            return MilvusVectorDB(config)
        case VectorDBProvider.CHROMADB:
            from .chromadb_client import ChromaVectorDB

            return ChromaVectorDB(config)
        case _:
            raise ValueError(f"Unsupported vector DB provider: {config.provider}")
