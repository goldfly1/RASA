
"""Codebase ingestion scanner for canonical model bootstrap.

Usage: python -m rasa.memory.ingest [--dry-run] [--clear]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg

PROJECT_ROOT = Path(os.getcwd())
SRC_DIRS = ["rasa", "cmd", "internal"]


def _pg_dsn() -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname=rasa_memory"


def _make_dotted_name(rel: Path) -> str:
    """Convert a relative path to a dotted module name."""
    s = str(rel).replace("\\", "/")
    if s.endswith("/__init__.py"):
        s = s[:-len("/__init__.py")]
    elif s.endswith(".py"):
        s = s[:-3]
    return s.replace("/", ".")


def _discover_python_nodes(root: Path) -> list[dict[str, Any]]:
    """Walk a directory and extract module/class/function nodes via ast."""
    nodes: list[dict[str, Any]] = []
    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" in py_file.parts or ".venv" in py_file.parts:
            continue
        rel = py_file.relative_to(PROJECT_ROOT)
        source = str(rel).replace("\\", "/")

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        module_name = _make_dotted_name(rel)
        module_id = str(uuid.uuid4())
        doc = ast.get_docstring(tree) or ""

        nodes.append({
            "id": module_id,
            "name": module_name,
            "node_type": "module",
            "path": source,
            "body": {"source_file": source, "docstring": doc[:500], "language": "python"},
            "outgoing_edges": [],
        })

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                class_doc = ast.get_docstring(node) or ""
                child_id = str(uuid.uuid4())
                nodes.append({
                    "id": child_id,
                    "name": f"{module_name}.{node.name}",
                    "node_type": "class",
                    "path": source,
                    "body": {"source_file": source, "docstring": class_doc[:500], "language": "python"},
                    "outgoing_edges": [],
                })
                for pn in nodes:
                    if pn["id"] == module_id:
                        pn["outgoing_edges"].append(child_id)
                        break
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_doc = ast.get_docstring(node) or ""
                child_id = str(uuid.uuid4())
                nodes.append({
                    "id": child_id,
                    "name": f"{module_name}.{node.name}",
                    "node_type": "function",
                    "path": source,
                    "body": {"source_file": source, "docstring": func_doc[:500], "language": "python"},
                    "outgoing_edges": [],
                })
                for pn in nodes:
                    if pn["id"] == module_id:
                        pn["outgoing_edges"].append(child_id)
                        break

    return nodes


GO_FUNC_RE = re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)\(", re.MULTILINE)
GO_TYPE_RE = re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE)
GO_INTERFACE_RE = re.compile(r"^type\s+(\w+)\s+interface", re.MULTILINE)


def _discover_go_nodes(root: Path) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for go_file in sorted(root.rglob("*.go")):
        rel = go_file.relative_to(PROJECT_ROOT)
        source = str(rel).replace("\\", "/")

        try:
            text = go_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        pkg_match = re.search(r"^package\s+(\w+)", text, re.MULTILINE)
        pkg_name = pkg_match.group(1) if pkg_match else go_file.parent.name
        pkg_id = str(uuid.uuid4())

        nodes.append({
            "id": pkg_id,
            "name": pkg_name,
            "node_type": "package",
            "path": source,
            "body": {"source_file": source, "docstring": "", "language": "go"},
            "outgoing_edges": [],
        })

        for match in GO_FUNC_RE.finditer(text):
            child_id = str(uuid.uuid4())
            nodes.append({
                "id": child_id,
                "name": f"{pkg_name}.{match.group(1)}",
                "node_type": "function",
                "path": source,
                "body": {"source_file": source, "docstring": "", "language": "go"},
                "outgoing_edges": [],
            })
            for pn in nodes:
                if pn["id"] == pkg_id:
                    pn["outgoing_edges"].append(child_id)
                    break

        for match in GO_TYPE_RE.finditer(text):
            child_id = str(uuid.uuid4())
            nodes.append({
                "id": child_id,
                "name": f"{pkg_name}.{match.group(1)}",
                "node_type": "struct",
                "path": source,
                "body": {"source_file": source, "docstring": "", "language": "go"},
                "outgoing_edges": [],
            })
            for pn in nodes:
                if pn["id"] == pkg_id:
                    pn["outgoing_edges"].append(child_id)
                    break

        for match in GO_INTERFACE_RE.finditer(text):
            child_id = str(uuid.uuid4())
            nodes.append({
                "id": child_id,
                "name": f"{pkg_name}.{match.group(1)}",
                "node_type": "interface",
                "path": source,
                "body": {"source_file": source, "docstring": "", "language": "go"},
                "outgoing_edges": [],
            })
            for pn in nodes:
                if pn["id"] == pkg_id:
                    pn["outgoing_edges"].append(child_id)
                    break

    return nodes


def ingest(dry_run: bool = False, clear_first: bool = False) -> int:
    all_nodes: list[dict[str, Any]] = []

    for dir_name in SRC_DIRS:
        d = PROJECT_ROOT / dir_name
        if not d.exists():
            print(f"[ingest] skipping missing dir: {dir_name}")
            continue
        print(f"[ingest] scanning {dir_name}/ ...")
        py_nodes = _discover_python_nodes(d)
        go_nodes = _discover_go_nodes(d)
        print(f"[ingest]   python: {len(py_nodes)} nodes, go: {len(go_nodes)} nodes")
        all_nodes.extend(py_nodes)
        all_nodes.extend(go_nodes)

    if dry_run:
        for n in all_nodes[:20]:
            lang = n["body"].get("language", "?")
            print(f"  [{lang}] {n['node_type']:10s} {n['name']:40s} {n['path']}")
        if len(all_nodes) > 20:
            print(f"  ... and {len(all_nodes) - 20} more")
        return len(all_nodes)

    try:
        with psycopg.connect(_pg_dsn()) as conn:
            with conn.cursor() as cur:
                if clear_first:
                    cur.execute("DELETE FROM embeddings WHERE node_id IN (SELECT id FROM canonical_nodes)")
                    cur.execute("DELETE FROM canonical_nodes")
                    print("[ingest] cleared existing nodes")

                for node in all_nodes:
                    cur.execute(
                        """INSERT INTO canonical_nodes (id, name, node_type, path, body, outgoing_edges)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (name, node_type) DO UPDATE SET
                             path = EXCLUDED.path,
                             body = EXCLUDED.body,
                             outgoing_edges = EXCLUDED.outgoing_edges""",
                        (node["id"], node["name"], node["node_type"],
                         node["path"], json.dumps(node["body"]), node["outgoing_edges"]),
                    )
            conn.commit()
        print(f"[ingest] wrote {len(all_nodes)} nodes to canonical_nodes")
    except Exception as exc:
        print(f"[ingest] DB write failed: {exc}", file=sys.stderr)
        return 0

    return len(all_nodes)


def main() -> None:
    parser = argparse.ArgumentParser(description="RASA codebase ingestion scanner")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    count = ingest(dry_run=args.dry_run, clear_first=args.clear)
    print(f"[ingest] total nodes: {count}")


if __name__ == "__main__":
    main()
