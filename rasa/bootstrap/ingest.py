"""Bootstrap & ingestion pipeline for RASA.

One-shot CLI for onboarding a repository: AST extraction, canonical model seeding,
vector index construction, soul sheet loading, and baseline freezing.

Usage:
  python -m rasa.bootstrap.ingest --repo /path/to/target-repo
  python -m rasa.bootstrap.ingest --repo . --souls-only
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
SOULS_DIR = PROJECT_ROOT / "souls"


def _pg_dsn(dbname: str = "rasa_memory") -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname={dbname}"


# --- Python AST extraction ---

def _extract_python_imports(file_path: Path) -> list[dict[str, str]]:
    """Extract import statements from a Python file."""
    imports: list[dict[str, str]] = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"type": "import", "name": alias.name, "source": str(file_path)})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append({"type": "import_from", "name": f"{module}.{alias.name}", "source": str(file_path)})
    except (SyntaxError, UnicodeDecodeError, OSError):
        pass
    return imports


def _extract_python_functions(file_path: Path) -> list[dict[str, Any]]:
    """Extract function and class definitions from a Python file."""
    items: list[dict[str, Any]] = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                items.append({
                    "type": "function",
                    "name": node.name,
                    "lineno": node.lineno,
                    "source": str(file_path),
                })
            elif isinstance(node, ast.ClassDef):
                items.append({
                    "type": "class",
                    "name": node.name,
                    "lineno": node.lineno,
                    "source": str(file_path),
                })
    except (SyntaxError, UnicodeDecodeError, OSError):
        pass
    return items


# --- Go dependency extraction (simple regex) ---

def _extract_go_imports(file_path: Path) -> list[dict[str, str]]:
    """Extract Go import blocks from a file using regex."""
    imports: list[dict[str, str]] = []
    try:
        content = file_path.read_text(encoding="utf-8")
        # Match import blocks
        for match in re.finditer(r'import\s*\(([^)]+)\)', content, re.DOTALL):
            for line in match.group(1).split("\n"):
                line = line.strip().strip('"')
                if line and not line.startswith("//"):
                    imports.append({"type": "import", "name": line, "source": str(file_path)})
        # Single-line imports
        for match in re.finditer(r'import\s+"([^"]+)"', content):
            imports.append({"type": "import", "name": match.group(1), "source": str(file_path)})
    except (UnicodeDecodeError, OSError):
        pass
    return imports


# --- Canonical model population ---

def _write_canonical_node(
    cur,
    node_id: str,
    name: str,
    node_type: str,
    file_path: str,
    language: str,
    metadata: dict | None = None,
) -> None:
    cur.execute(
        """INSERT INTO canonical_model_nodes (id, name, type, file_path, language, metadata)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO UPDATE SET
             name = EXCLUDED.name, type = EXCLUDED.type,
             file_path = EXCLUDED.file_path, metadata = EXCLUDED.metadata""",
        (node_id, name, node_type, file_path, language, json.dumps(metadata or {})),
    )


def _write_canonical_edge(
    cur,
    edge_id: str,
    from_id: str,
    to_id: str,
    edge_type: str,
    metadata: dict | None = None,
) -> None:
    cur.execute(
        """INSERT INTO canonical_model_edges (id, from_node_id, to_node_id, type, metadata)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        (edge_id, from_id, to_id, edge_type, json.dumps(metadata or {})),
    )


def _ingest_repo(repo_path: Path, dbname: str = "rasa_memory") -> dict[str, int]:
    """Scan a repo and populate the canonical model in PostgreSQL."""
    stats = {"python_files": 0, "go_files": 0, "nodes": 0, "edges": 0}

    try:
        with psycopg.connect(_pg_dsn(dbname)) as conn:
            with conn.cursor() as cur:
                # Ingest Python files
                for py_file in repo_path.rglob("*.py"):
                    if "__pycache__" in str(py_file) or ".pytest_cache" in str(py_file):
                        continue
                    stats["python_files"] += 1

                    # File-level node
                    file_node_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"file:{py_file}"))
                    rel_path = str(py_file.relative_to(repo_path))
                    _write_canonical_node(
                        cur, file_node_id, rel_path, "file", rel_path, "python",
                        metadata={"absolute_path": str(py_file), "size_bytes": py_file.stat().st_size},
                    )
                    stats["nodes"] += 1

                    # Function/class nodes
                    for item in _extract_python_functions(py_file):
                        node_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{item['type']}:{py_file}:{item['name']}"))
                        _write_canonical_node(
                            cur, node_id, item["name"], item["type"],
                            str(py_file.relative_to(repo_path)), "python",
                            metadata={"lineno": item["lineno"]},
                        )
                        stats["nodes"] += 1
                        # Edge from file to function/class
                        edge_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"edge:{file_node_id}:{node_id}"))
                        _write_canonical_edge(cur, edge_id, file_node_id, node_id, "contains")
                        stats["edges"] += 1

                    # Import edges
                    for imp in _extract_python_imports(py_file):
                        target_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"module:{imp['name']}"))
                        edge_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"edge:{file_node_id}:{target_id}:import"))
                        _write_canonical_edge(cur, edge_id, file_node_id, target_id, "imports")
                        stats["edges"] += 1

                # Ingest Go files
                for go_file in repo_path.rglob("*.go"):
                    if "vendor" in str(go_file):
                        continue
                    stats["go_files"] += 1

                    file_node_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"file:{go_file}"))
                    rel_path = str(go_file.relative_to(repo_path))
                    _write_canonical_node(
                        cur, file_node_id, rel_path, "file", rel_path, "go",
                        metadata={"absolute_path": str(go_file), "size_bytes": go_file.stat().st_size},
                    )
                    stats["nodes"] += 1

                    for imp in _extract_go_imports(go_file):
                        target_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"module:{imp['name']}"))
                        edge_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"edge:{file_node_id}:{target_id}:import"))
                        _write_canonical_edge(cur, edge_id, file_node_id, target_id, "imports")
                        stats["edges"] += 1

            conn.commit()
    except Exception as exc:
        print(f"[bootstrap] ingestion error: {exc}", file=sys.stderr)

    return stats


# --- Soul sheet ingestion ---

def _ingest_souls(dbname: str = "rasa_memory") -> list[str]:
    """Load and validate all soul sheets, store in PostgreSQL, emit souls.loaded."""
    from rasa.agent.soul import SoulLoader

    loader = SoulLoader()
    loaded: list[str] = []

    print("[bootstrap] loading soul sheets...", flush=True)
    for soul_id in sorted(loader.list_all()):
        try:
            soul = loader.load(soul_id)
            loaded.append(soul_id)
            print(f"[bootstrap]   {soul_id} v{soul.soul_version} ({soul.agent_role})", flush=True)
        except Exception as exc:
            print(f"[bootstrap]   {soul_id} FAILED: {exc}", file=sys.stderr)

    # Emit souls.loaded notification
    try:
        with psycopg.connect(_pg_dsn(dbname)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_notify('souls_loaded', %s)",
                    (json.dumps({"soul_ids": loaded, "count": len(loaded), "timestamp": time.time()}),),
                )
            conn.commit()
        print(f"[bootstrap] emitted souls.loaded ({len(loaded)} souls)", flush=True)
    except Exception as exc:
        print(f"[bootstrap] NOTIFY failed: {exc}", file=sys.stderr)

    return loaded


# --- Baseline freezing ---

def _freeze_baseline(dbname: str = "rasa_memory") -> str:
    """Snapshot the canonical model as baseline_v1 and write to baselines table."""
    baseline_id = str(uuid.uuid4())
    try:
        with psycopg.connect(_pg_dsn(dbname)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO baselines (id, version, snapshot_data, created_at)
                       VALUES (%s, %s, %s, NOW())""",
                    (
                        baseline_id,
                        "baseline_v1",
                        json.dumps({
                            "frozen_at": time.time(),
                            "description": "Initial bootstrap baseline",
                        }),
                    ),
                )
            conn.commit()
        print(f"[bootstrap] baseline frozen: {baseline_id}", flush=True)
    except Exception as exc:
        print(f"[bootstrap] baseline freeze failed: {exc}", file=sys.stderr)
    return baseline_id


# --- Main ---

def run_bootstrap(
    repo_path: str,
    dbname: str = "rasa_memory",
    souls_only: bool = False,
    skip_ingestion: bool = False,
) -> int:
    repo = Path(repo_path).resolve()
    if not repo.exists():
        print(f"[bootstrap] repo not found: {repo}", file=sys.stderr)
        return 1

    print(f"[bootstrap] onboarding {repo}", flush=True)
    start = time.time()

    if not skip_ingestion:
        # Step 1: Ingest code
        print("[bootstrap] extracting AST and dependencies...", flush=True)
        stats = _ingest_repo(repo, dbname)
        print(
            f"[bootstrap] ingested: {stats['python_files']} .py + {stats['go_files']} .go "
            f"-> {stats['nodes']} nodes, {stats['edges']} edges",
            flush=True,
        )

    # Step 2: Load soul sheets
    souls = _ingest_souls(dbname)
    if not souls:
        print("[bootstrap] WARNING: no souls loaded", file=sys.stderr)

    if souls_only:
        elapsed = time.time() - start
        print(f"[bootstrap] souls-only bootstrap complete ({elapsed:.1f}s)", flush=True)
        return 0

    # Step 3: Freeze baseline
    _freeze_baseline(dbname)

    elapsed = time.time() - start
    print(f"[bootstrap] complete: {len(souls)} souls, {elapsed:.1f}s", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RASA bootstrap & ingestion")
    parser.add_argument("--repo", default=str(PROJECT_ROOT), help="Path to target repo")
    parser.add_argument("--db", default="rasa_memory", help="Target database for canonical model")
    parser.add_argument("--souls-only", action="store_true", help="Only load soul sheets (skip AST ingestion)")
    parser.add_argument("--skip-ingestion", action="store_true", help="Skip code ingestion")
    args = parser.parse_args()

    sys.exit(run_bootstrap(args.repo, args.db, args.souls_only, args.skip_ingestion))


if __name__ == "__main__":
    main()
