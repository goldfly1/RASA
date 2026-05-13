"""pgvector helpers: upsert embeddings and semantic search against PostgreSQL."""

from __future__ import annotations

from typing import Any

import psycopg
try:
    from psycopg_pool import ConnectionPool
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]


async def upsert_embedding(
    pool: "ConnectionPool",
    node_id: str,
    model: str,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
) -> None:
    """Insert or update an embedding row with a pgvector cast."""
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO embeddings (id, node_id, model, chunk_index, chunk_text, embedding)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s::vector)
            ON CONFLICT (node_id, model, chunk_index) DO UPDATE SET
                chunk_text = EXCLUDED.chunk_text,
                embedding = EXCLUDED.embedding
            """,
            (node_id, model, chunk_index, chunk_text, embedding),
        )


async def semantic_search(
    pool: "ConnectionPool",
    query_embedding: list[float],
    k: int = 5,
    model_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Top-K cosine similarity search against the HNSW index."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT e.id, e.node_id, e.model, e.chunk_index, e.chunk_text,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM embeddings e
            WHERE (%s::text IS NULL OR e.model = %s)
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, model_filter, model_filter, query_embedding, k),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": row[0],
                "node_id": row[1],
                "model": row[2],
                "chunk_index": row[3],
                "chunk_text": row[4],
                "similarity": row[5],
            }
            for row in rows
        ]
