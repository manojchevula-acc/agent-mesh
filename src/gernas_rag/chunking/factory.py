"""Chunker factory."""

from ..config.chunking import ChunkingConfig, ChunkingStrategy
from .base import BaseChunker


def get_chunker(config: ChunkingConfig) -> BaseChunker:
    match config.strategy:
        case ChunkingStrategy.HIERARCHICAL:
            from .hierarchical import HierarchicalChunker

            return HierarchicalChunker(config)
        case ChunkingStrategy.FIXED_SIZE:
            from .fixed_size import FixedSizeChunker

            return FixedSizeChunker(config)
        case _:
            raise ValueError(f"Unsupported chunking strategy: {config.strategy}")
