"""Security scanners invoked after file writes in the agent sandbox."""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCANNERS_DIR = PROJECT_ROOT / "scanners"


@dataclass
class ScanResult:
    """Aggregated scanner output for a file or directory."""
    path: str
    passed: bool = True
    findings: list[dict[str, Any]] = field(default_factory=list)
    semgrep_raw: dict[str, Any] = field(default_factory=dict)
    secrets_raw: dict[str, Any] = field(default_factory=dict)


# --- Role-based scanner overlay loading ---

def _load_overlay(agent_role: str) -> dict[str, Any]:
    """Load role-specific scanner rules from scanners/{role}.yaml."""
    overlay_path = SCANNERS_DIR / f"{agent_role.lower()}.yaml"
    if not overlay_path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# --- Individual scanners ---

def run_semgrep(target_path: str, config: str = "auto") -> dict[str, Any]:
    """Run Semgrep on a file or directory. Returns findings."""
    try:
        env = dict(os.environ, SEMGREP_USER_HOME=str(PROJECT_ROOT / "data" / ".semgrep")); args = ["semgrep", "--config", config, "--no-automatic-scan", "--quiet", "--json", target_path]
        result = subprocess.run(
            args,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if result.returncode == 0 and not result.stdout.strip():
            return {"findings": [], "status": "clean"}
        try:
            data = json.loads(result.stdout)
            findings = data.get("results", [])
            return {
                "findings": [
                    {
                        "rule": f.get("check_id", "?"),
                        "message": f.get("extra", {}).get("message", ""),
                        "severity": f.get("extra", {}).get("severity", "WARNING"),
                        "path": f.get("path", ""),
                        "line": f.get("start", {}).get("line", 0),
                    }
                    for f in findings[:50]
                ],
                "count": len(findings),
                "status": "issues_found" if findings else "clean",
            }
        except (json.JSONDecodeError, KeyError):
            return {"findings": [], "status": "parse_error", "stderr": result.stderr[:500]}
    except FileNotFoundError:
        return {"status": "unavailable", "error": "semgrep not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "semgrep timed out"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def run_secrets_scan(target_path: str) -> dict[str, Any]:
    """Run detect-secrets on a file or directory. Returns findings."""
    try:
        result = subprocess.run(
            ["detect-secrets", "scan", "--all-files", target_path],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return {"findings": [], "status": "clean"}
        # Parse detect-secrets JSON output
        try:
            data = json.loads(result.stdout)
            results = data.get("results", {})
            findings_list = []
            for filepath, secrets in results.items():
                for secret in secrets:
                    findings_list.append({
                        "rule": f"secret:{secret.get('type', 'unknown')}",
                        "message": f"Potential secret found: {secret.get('type', '?')}",
                        "severity": "ERROR",
                        "path": filepath,
                        "line": secret.get("line_number", 0),
                        "action": "deny",
                    })
            return {
                "findings": findings_list[:50],
                "count": len(findings_list),
                "status": "issues_found" if findings_list else "clean",
            }
        except (json.JSONDecodeError, KeyError):
            if "ERROR" in output or "CRITICAL" in output:
                return {"findings": [{"message": output[:500], "action": "deny"}], "status": "issues_found"}
            return {"findings": [], "status": "clean"}
    except FileNotFoundError:
        return {"status": "unavailable", "error": "detect-secrets not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "detect-secrets timed out"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# --- Aggregated scans ---

def scan_file(file_path: str, agent_role: str = "") -> ScanResult:
    """Run all scanners on a single file. Returns ScanResult."""
    semgrep_result = run_semgrep(file_path)
    secrets_result = run_secrets_scan(file_path)

    all_findings = semgrep_result.get("findings", []) + secrets_result.get("findings", [])
    has_failures = any(
        f.get("action") == "deny" or f.get("severity") == "ERROR"
        for f in all_findings
    )

    return ScanResult(
        path=file_path,
        passed=not has_failures,
        findings=all_findings,
        semgrep_raw=semgrep_result,
        secrets_raw=secrets_result,
    )


def scan_directory(dir_path: str | Path, agent_role: str = "") -> ScanResult:
    """Run all scanners on every file in a directory tree. Returns aggregated ScanResult."""
    dir_path = Path(dir_path)
    all_findings: list[dict[str, Any]] = []
    semgrep_raw: dict[str, Any] = {"findings": [], "status": "clean"}
    secrets_raw: dict[str, Any] = {"findings": [], "status": "clean"}

    # Load role overlay for Semgrep config
    overlay = _load_overlay(agent_role) if agent_role else {}
    semgrep_config = overlay.get("semgrep_config", "auto")

    # Run semgrep on the whole directory (much faster than per-file)
    semgrep_raw = run_semgrep(str(dir_path), config=semgrep_config)
    all_findings.extend(semgrep_raw.get("findings", []))

    # Run detect-secrets on the whole directory
    secrets_raw = run_secrets_scan(str(dir_path))
    all_findings.extend(secrets_raw.get("findings", []))

    # Apply role-specific deny rules
    deny_patterns = overlay.get("deny_patterns", [])
    ignore_patterns = overlay.get("ignore_patterns", [])

    for finding in all_findings:
        finding_path = finding.get("path", "")
        # Skip ignored patterns
        if any(fnmatch.fnmatch(finding_path, p) for p in ignore_patterns):
            finding["action"] = "allow"
            continue
        # Mark deny patterns
        if any(fnmatch.fnmatch(finding_path, p) for p in deny_patterns):
            finding["action"] = "deny"

    has_denials = any(f.get("action") == "deny" for f in all_findings)
    has_errors = any(f.get("severity") == "ERROR" for f in all_findings if f.get("action") != "allow")

    return ScanResult(
        path=str(dir_path),
        passed=not (has_denials or has_errors),
        findings=all_findings,
        semgrep_raw=semgrep_raw,
        secrets_raw=secrets_raw,
    )
