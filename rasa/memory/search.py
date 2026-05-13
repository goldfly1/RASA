
"""Semantic search over pgvector embeddings for agent context assembly."""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg


def _pg_dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_memory"


def _embed_query(text: str) -> list[float]:
    """Embed a query string using nomic-embed-text via Ollama."""
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.embeddings.create(model="nomic-embed-text:latest", input=text[:8000])
        return resp.data[0].embedding
    except Exception:
        return []


def semantic_search(
    query: str,
    top_k: int = 5,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """Search embeddings for semantically similar canonical nodes.

    Returns nodes with their chunk text and cosine distance.
    """
    embedding = _embed_query(query)
    if not embedding:
        return []

    # pgvector <=> is cosine distance; 1 - distance = similarity
    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT cn.id, cn.name, cn.node_type, cn.path, cn.body,
                              e.chunk_text, 1 - (e.embedding <=> %s::vector) AS similarity
                       FROM embeddings e
                       JOIN canonical_nodes cn ON e.node_id = cn.id
                       WHERE 1 - (e.embedding <=> %s::vector) > %s
                       ORDER BY similarity DESC
                       LIMIT %s""",
                    (embedding, embedding, min_similarity, top_k),
                )
                rows = cur.fetchall()
                return [
                    {
                        "id": str(row[0]),
                        "name": row[1],
                        "node_type": row[2],
                        "path": row[3],
                        "body": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
                        "chunk_text": row[5],
                        "similarity": float(row[6]),
                    }
                    for row in rows
                ]
    except Exception as exc:
        print(f"[search] pgvector query failed: {exc}", flush=True)
        return []



def embed_all_nodes(batch_size: int = 10) -> int:
    """Generate embeddings for all canonical nodes that lack them. Returns count embedded."""
    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT cn.id, cn.name, cn.body->>'docstring'
                       FROM canonical_nodes cn
                       WHERE NOT EXISTS (
                         SELECT 1 FROM embeddings e WHERE e.node_id = cn.id
                       )
                       LIMIT 200"""
                )
                rows = cur.fetchall()
    except Exception as exc:
        print(f"[search] embed_all failed to query: {exc}")
        return 0

    if not rows:
        print("[search] all nodes already embedded")
        return 0

    embedded = 0
    from openai import OpenAI
    client = OpenAI()

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = []
        ids_in_batch = []
        for node_row in batch:
            node_id, name, docstring = node_row
            text = f"{name}: {docstring or ''}"[:2000]
            texts.append(text)
            ids_in_batch.append(node_id)

        try:
            resp = client.embeddings.create(model="nomic-embed-text:latest", input=texts)
            embeddings = [d.embedding for d in resp.data]

            with psycopg.connect(_pg_dsn()) as conn:
                with conn.cursor() as cur:
                    import uuid as _uuid
                    for j, (node_id, emb) in enumerate(zip(ids_in_batch, embeddings)):
                        cur.execute(
                            """INSERT INTO embeddings (id, node_id, model, chunk_index, chunk_text, embedding, created_at)
                               VALUES (%s, %s, %s, 0, %s, %s, NOW())
                               ON CONFLICT DO NOTHING""",
                            (str(_uuid.uuid4()), node_id, "nomic-embed-text:latest", texts[j], emb),
                        )
                conn.commit()
            embedded += len(batch)
            print(f"[search] embedded {embedded}/{len(rows)} nodes...")
        except Exception as exc:
            print(f"[search] embed batch failed: {exc}")
            break

    return embedded


def get_context_for_task(task_title: str, task_description: str = "") -> dict[str, Any]:
    """Build memory context for a task: graph excerpt + short term summary."""
    query = f"{task_title}. {task_description}"[:500]
    results = semantic_search(query, top_k=8)

    if not results:
        return {
            "short_term_summary": "",
            "graph_excerpt": "",
            "semantic_matches": [],
        }

    # Build graph excerpt from top results
    graph_lines = []
    seen_modules = set()
    for r in results:
        if r["node_type"] == "module" and r["name"] not in seen_modules:
            seen_modules.add(r["name"])
            doc = r["body"].get("docstring", "") if isinstance(r["body"], dict) else ""
            graph_lines.append(f"[{r['node_type']}] {r['name']}: {doc[:100]}")
        else:
            graph_lines.append(f"[{r['node_type']}] {r['name']} (in {r['path']})")

    return {
        "short_term_summary": results[0]["chunk_text"][:500] if results else "",
        "graph_excerpt": "\n".join(graph_lines[:10]),
        "semantic_matches": [
            {"name": r["name"], "type": r["node_type"], "similarity": r["similarity"]}
            for r in results[:5]
        ],
    }
