"""Ingest documentation files into the lore knowledge base (canonical_nodes + embeddings).

Usage:
    python scripts/ingest_lore.py --files README.md docs/*.md souls/*.yaml
    python scripts/ingest_lore.py --all                    # all project docs
    python scripts/ingest_lore.py --files *.md --embed      # with embeddings
    python scripts/ingest_lore.py --files *.md --dry-run    # preview only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from openai import AsyncOpenAI

PROJECT_ROOT = Path(__file__).parent.parent

# ── DB config ──

def _memory_dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "8764")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_memory"


def _connect():
    return psycopg.connect(_memory_dsn())


# ── Embedding helpers ──

EMBED_MODEL = "text-embedding-3-small"
MAX_CHUNK_CHARS = 6000


async def _generate_embeddings(texts: list[str], model: str = EMBED_MODEL) -> list[list[float]] | None:
    """Generate embeddings via OpenAI-compatible API. Returns None on failure."""
    try:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OLLAMA_API_KEY", "ollama")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in resp.data]
    except Exception as e:
        print(f"  [embedding failed: {e}]", flush=True)
        return None


# ── File reading and chunking ──

def _read_file(path: Path) -> str:
    """Read a file, trying UTF-8 then latin-1."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _chunk_markdown(content: str, source: str) -> list[dict[str, Any]]:
    """Split markdown into sections by ## headings."""
    lines = content.split("\n")
    sections: list[dict[str, Any]] = []
    current_heading = "(preamble)"
    current_lines: list[str] = []

    for line in lines:
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_lines:
                sections.append({
                    "heading": current_heading,
                    "text": "\n".join(current_lines).strip(),
                    "anchor": re.sub(r"[^a-zA-Z0-9_-]", "_", current_heading.lower()),
                })
            current_heading = m.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "heading": current_heading,
            "text": "\n".join(current_lines).strip(),
            "anchor": re.sub(r"[^a-zA-Z0-9_-]", "_", current_heading.lower()),
        })

    return sections


def _chunk_by_size(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[dict[str, Any]]:
    """Split text into fixed-size chunks for non-markdown files."""
    chunks = []
    for i in range(0, len(text), max_chars):
        chunk_text = text[i:i + max_chars]
        chunks.append({
            "heading": f"part-{i // max_chars + 1}",
            "text": chunk_text,
            "anchor": f"part_{i // max_chars + 1}",
        })
    return chunks


def _chunk_file(path: Path, content: str) -> list[dict[str, Any]]:
    """Split file content into ingestible chunks based on extension."""
    if path.suffix in (".md", ".markdown", ".rst"):
        sections = _chunk_markdown(content, str(path))
        # Filter out empty sections
        return [s for s in sections if s["text"]]
    else:
        return _chunk_by_size(content)


# ── Ingestion ──

def _node_name(path: Path, section: dict[str, Any] | None = None) -> str:
    """Generate a unique name for the canonical node."""
    rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    if section:
        return f"{rel}#{section['anchor']}"
    return str(rel)


def ingest_file(
    conn: psycopg.Connection,
    path: Path,
    sections: list[dict[str, Any]],
    dry_run: bool = False,
) -> tuple[int, int]:
    """Ingest a file into canonical_nodes. Returns (inserted, skipped)."""
    rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    now = datetime.now(timezone.utc)
    inserted = 0
    skipped = 0

    if not sections:
        print(f"  ⚠ no content sections found", flush=True)
        return (0, 0)

    # Parent node for the file
    parent_name = _node_name(path)
    parent_body = {
        "source": str(rel),
        "type": path.suffix.lstrip("."),
        "description": f"Documentation: {rel}",
        "sections": len(sections),
    }

    if dry_run:
        print(f"  would insert parent: {parent_name}", flush=True)
    else:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO canonical_nodes (id, node_type, name, path, body, created_at, updated_at)
                   VALUES (%s, 'manual', %s, %s, %s, %s, %s)
                   ON CONFLICT (name, node_type) DO NOTHING
                   RETURNING id""",
                (uuid.uuid4(), parent_name, str(rel),
                 json.dumps(parent_body), now, now),
            )
            row = cur.fetchone()
            if row:
                parent_id = str(row[0])
                inserted += 1
            else:
                parent_id = None
                skipped += 1

    # Child nodes for each section
    child_ids: list[str] = []
    for sec in sections:
        child_name = _node_name(path, sec)
        child_body = {
            "source": str(rel),
            "heading": sec["heading"],
            "text": sec["text"],
            "type": path.suffix.lstrip("."),
        }

        if dry_run:
            print(f"  would insert section: {child_name} ({len(sec['text'])} chars)", flush=True)
            continue

        with conn.cursor() as cur:
            child_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO canonical_nodes (id, node_type, name, path, body, created_at, updated_at)
                   VALUES (%s, 'manual_section', %s, %s, %s, %s, %s)
                   ON CONFLICT (name, node_type) DO NOTHING
                   RETURNING id""",
                (child_id, child_name, str(rel),
                 json.dumps(child_body), now, now),
            )
            row = cur.fetchone()
            if row:
                inserted += 1
                child_ids.append(child_id)
            else:
                skipped += 1

    # Update parent's outgoing_edges to point to children
    if not dry_run and child_ids and parent_id:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE canonical_nodes SET outgoing_edges = %s, updated_at = %s WHERE id = %s",
                (child_ids, now, parent_id),
            )

    # Commit after each file
    if not dry_run:
        conn.commit()

    return (inserted, skipped)


async def ingest_files(
    paths: list[Path],
    embed: bool = False,
    dry_run: bool = False,
    embed_model: str = EMBED_MODEL,
) -> None:
    """Ingest multiple files into the lore store."""
    conn = _connect()
    total_inserted = 0
    total_skipped = 0
    total_files = len(paths)

    all_embed_texts: list[tuple[str, str, int, str]] = []  # (node_name, node_id, chunk_index, text)

    for i, path in enumerate(paths):
        if not path.exists():
            print(f"[{i+1}/{total_files}] SKIP {path} (not found)", flush=True)
            continue

        try:
            content = _read_file(path)
        except Exception as e:
            print(f"[{i+1}/{total_files}] ERROR {path}: {e}", flush=True)
            continue

        if not content.strip():
            print(f"[{i+1}/{total_files}] SKIP {path} (empty)", flush=True)
            continue

        sections = _chunk_file(path, content)
        if not sections:
            print(f"[{i+1}/{total_files}] SKIP {path} (no content sections)", flush=True)
            continue

        inserted, skipped = ingest_file(conn, path, sections, dry_run)
        total_inserted += inserted
        total_skipped += skipped

        action = "DRY-RUN" if dry_run else "OK"
        print(f"[{i+1}/{total_files}] {action} {path} ({inserted} inserted, {skipped} skipped, {len(sections)} sections)", flush=True)

        # Collect texts for embedding
        if embed and not dry_run:
            rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
            for si, sec in enumerate(sections):
                child_name = _node_name(path, sec)
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM canonical_nodes WHERE name = %s AND node_type = 'manual_section'",
                        (child_name,),
                    )
                    row = cur.fetchone()
                    if row:
                        all_embed_texts.append((child_name, str(row[0]), si, sec["text"]))

    conn.close()

    total = total_inserted + total_skipped
    print(f"\n{'DRY-RUN: ' if dry_run else ''}Ingested {total_inserted} new nodes, {total_skipped} skipped across {total_files} files", flush=True)

    # Generate embeddings if requested
    if embed and all_embed_texts and not dry_run:
        print(f"\nGenerating embeddings for {len(all_embed_texts)} sections...", flush=True)
        conn = _connect()
        inserted_emb = 0
        batch_size = 10
        for i in range(0, len(all_embed_texts), batch_size):
            batch = all_embed_texts[i:i + batch_size]
            texts = [t[3][:4000] for t in batch]  # truncate for nomic-embed-text 2048-token context
            embeddings = await _generate_embeddings(texts, model=embed_model)
            if not embeddings:
                print(f"  embedding failed at batch {i // batch_size}, skipping rest", flush=True)
                break
            for (child_name, node_id, chunk_idx, chunk_text), emb in zip(batch, embeddings):
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO embeddings (id, node_id, model, chunk_index, chunk_text, embedding)
                           VALUES (gen_random_uuid(), %s, %s, %s, %s, %s::vector)
                           ON CONFLICT (node_id, model, chunk_index) DO UPDATE SET
                               chunk_text = EXCLUDED.chunk_text,
                               embedding = EXCLUDED.embedding""",
                        (node_id, embed_model, chunk_idx, chunk_text, emb),
                    )
                inserted_emb += 1
            if (i // batch_size) % 3 == 0:
                conn.commit()
        conn.commit()
        conn.close()
        print(f"Inserted/updated {inserted_emb} embeddings", flush=True)


# ── All-project-docs helper ──

def _all_doc_files() -> list[Path]:
    """Return all project documentation files."""
    patterns = [
        "*.md",
        "docs/*.md",
        "schema/**/*.md",
        "souls/*.yaml",
        ".hermes/*.md",
    ]
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for p in PROJECT_ROOT.glob(pattern):
            if p.is_file() and p not in seen:
                files.append(p)
                seen.add(p)
    return sorted(files)


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="Ingest documentation into lore knowledge base")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--files", nargs="+", help="File paths or glob patterns to ingest")
    group.add_argument("--all", action="store_true", help="Ingest all project documentation files")
    parser.add_argument("--embed", action="store_true", help="Generate embeddings for ingested content")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be ingested without writing")
    parser.add_argument("--embed-model", default="text-embedding-3-small", help="Embedding model")
    args = parser.parse_args()

    embed_model = args.embed_model

    if args.all:
        paths = _all_doc_files()
    else:
        paths = []
        for pattern in args.files:
            matched = list(PROJECT_ROOT.glob(pattern))
            if matched:
                paths.extend(matched)
            else:
                p = Path(pattern)
                if p.exists():
                    paths.append(p)
        # Deduplicate and sort
        paths = sorted(set(paths))

    if not paths:
        print("No files matched.", flush=True)
        sys.exit(1)

    print(f"Found {len(paths)} file(s) to ingest{' (dry-run)' if args.dry_run else ''}", flush=True)
    for p in paths:
        rel = p.relative_to(PROJECT_ROOT) if p.is_relative_to(PROJECT_ROOT) else p
        print(f"  {rel}", flush=True)

    asyncio.run(ingest_files(paths, embed=args.embed, dry_run=args.dry_run, embed_model=embed_model))


if __name__ == "__main__":
    main()
