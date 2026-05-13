import subprocess
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

SERVICES = [
    ("PostgreSQL", ["pg_isready", "-h", "localhost", "-U", "postgres"]),
    ("Redis", ["redis-cli", "ping"]),
    ("Ollama", ["ollama", "list"]),
]

def check_pg_via_psycopg():
    try:
        import psycopg
        pw = os.environ.get("RASA_DB_PASSWORD", "8764")
        c = psycopg.connect(
            host=os.environ.get("RASA_DB_HOST", "localhost"),
            port=int(os.environ.get("RASA_DB_PORT", "5432")),
            user=os.environ.get("RASA_DB_USER", "postgres"),
            password=pw,
            dbname="rasa_orch",
            connect_timeout=5,
        )
        c.close()
        return True, "psycopg connected"
    except Exception as e:
        return False, str(e)[:100]

def check_service(name, cmd):
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=creationflags)
        return r.returncode == 0, r.stdout.strip()[:100]
    except Exception as e:
        return False, str(e)[:100]

def launch_all(progress_callback=None):
    results = []
    for name, cmd in SERVICES:
        if progress_callback:
            progress_callback(f"Checking {name}...")
        ok, msg = check_service(name, cmd)
        if not ok and name == "PostgreSQL":
            if progress_callback:
                progress_callback(f"  pg_isready failed, trying psycopg...")
            ok, msg = check_pg_via_psycopg()
        results.append((name, ok, msg))
        if progress_callback:
            progress_callback(f"  {name}: {'OK' if ok else 'MISSING'} - {msg}")
    return results
