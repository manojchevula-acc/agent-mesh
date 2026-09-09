"""MAF workflow orchestration for the retrieval pipeline.

This package is the *only* place in ``gernas_rag`` that imports
``agent_framework``. Everything else — embeddings, vector DB, reranking,
freshness, generation — stays framework-neutral, which is what lets the same
service be driven by a MAF agent, an MCP client, or plain HTTP.

``RetrievalPipeline`` builds this workflow once at construction and delegates every
``retrieve()`` call to it. There is no alternative implementation and no feature
flag: this is the retrieval path.
"""

from .build import build_retrieval_workflow
from .state import RagState

__all__ = ["RagState", "build_retrieval_workflow"]
