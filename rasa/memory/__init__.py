
"""Memory subsystem """
from rasa.memory.embedder import embed_loop, main

try:
    from rasa.memory.pgvector import semantic_search, upsert_embedding
except ImportError:
    semantic_search = None  # type: ignore
    upsert_embedding = None  # type: ignore

__all__ = ["embed_loop", "main", "semantic_search", "upsert_embedding"]
