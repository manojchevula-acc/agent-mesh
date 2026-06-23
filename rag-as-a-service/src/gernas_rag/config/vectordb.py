"""Vector database configuration."""

from enum import Enum

from pydantic import BaseModel


class VectorDBProvider(str, Enum):
    QDRANT = "qdrant"  # Primary — native hybrid search
    MILVUS = "milvus"  # Alternative — billion-scale
    CHROMADB = "chromadb"  # Dev/test only


class VectorDBConfig(BaseModel):
    provider: VectorDBProvider = VectorDBProvider.QDRANT
    collection_name: str = "fab_gernas_docs"

    # ── Qdrant ───────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_prefer_grpc: bool = False
    # Set to a local directory path (e.g. ./qdrant_storage) to use the
    # embedded in-process engine — no server or Docker required.
    qdrant_path: str | None = None

    # ── Milvus ───────────────────────────────────────────────────────
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_token: str | None = None

    # ── ChromaDB ─────────────────────────────────────────────────────
    chroma_path: str = "./chroma_db"
    chroma_host: str | None = None  # Remote ChromaDB
    chroma_port: int = 8001

    # ── Shared ───────────────────────────────────────────────────────
    dense_vector_size: int = 1024
    distance_metric: str = "cosine"  # 'cosine' | 'dot' | 'euclidean'
    replication_factor: int = 1  # Set to 2+ for production
    on_disk_payload: bool = True
