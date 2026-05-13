"""Benchmark regression runner for RASA agents."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import psycopg
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
BASELINES_DIR = PROJECT_ROOT / "benchmarks" / "baselines"


def _pg_dsn(dbname: str = "rasa_eval") -> str:
    host = os.environ.get("RASA_DB_HOST", "localhost")
    port = os.environ.get("RASA_DB_PORT", "5432")
    user = os.environ.get("RASA_DB_USER", "postgres")
    password = os.environ.get("RASA_DB_PASSWORD", "")
    return f"host={host} port={port} user={user} password={password} dbname={dbname}"


def _load_benchmarks(benchmark_id: str | None = None) -> list[dict[str, Any]]:
    specs = []
    for p in sorted(BENCHMARKS_DIR.glob("*.yaml")):
        with open(p) as f:
            spec = yaml.safe_load(f)
        if benchmark_id and spec.get("benchmark_id") != benchmark_id:
            continue
        specs.append(spec)
    return specs


def _score_output(output: str, spec: dict) -> dict[str, Any]:
    expected = spec.get("expected_patterns", [])
    forbidden = spec.get("forbidden_patterns", [])

    matches = sum(1 for pat in expected if re.search(pat, output, re.IGNORECASE))
    pattern_score = matches / len(expected) if expected else 1.0

    violations = sum(1 for pat in forbidden if re.search(pat, output, re.IGNORECASE))
    violation_penalty = violations / len(forbidden) if forbidden else 0

    scoring = spec.get("scoring", {})
    pattern_weight = scoring.get("pattern_match_weight", 0.7)
    lint_weight = scoring.get("lint_pass_weight", 0.3)

    lint_pass = 0.0 if violations > 0 else 1.0
    score = (pattern_score * pattern_weight) + (lint_pass * lint_weight)
    score = max(0.0, min(1.0, score - violation_penalty))
    passed = score >= scoring.get("min_passing_score", 0.6)

    return {
        "score": round(score, 4),
        "passed": passed,
        "pattern_score": round(pattern_score, 4),
        "matches": matches,
        "expected_count": len(expected),
        "violations": violations,
        "forbidden_count": len(forbidden),
    }


async def _run_agent(soul_id: str, prompt: str) -> str:
    from rasa.llm_gateway.client import GatewayClient
    from rasa.agent.soul import SoulLoader

    loader = SoulLoader()
    soul_obj = loader.load(soul_id)
    soul = soul_obj.raw

    gateway = GatewayClient()
    try:
        result = await gateway.complete(
            system_prompt=soul.get("prompt", {}).get("system_template", "You are a coding agent."),
            user_prompt=prompt,
            tier=soul.get("model", {}).get("default_tier", "standard"),
            temperature=0.0,
            max_tokens=soul.get("model", {}).get("max_tokens", 4096),
        )
        return result.get("content", "")
    finally:
        await gateway.close()


def _write_eval(soul_id: str, benchmark_id: str, score_data: dict, duration_ms: float, model: str = "") -> None:
    try:
        with psycopg.connect(_pg_dsn("rasa_eval")) as conn:
            with conn.cursor() as cur:
                record_id = str(uuid.uuid4())
                meta = {
                    "duration_ms": duration_ms,
                    "benchmark_id": benchmark_id,
                    "pattern_score": score_data["pattern_score"],
                    "matches": score_data["matches"],
                    "violations": score_data["violations"],
                    "model": model,
                }
                cur.execute(
                    """INSERT INTO evaluation_records (id, soul_id, score, passed, metadata, created_at)
                       VALUES (%s, %s, %s, %s, %s, NOW())""",
                    (record_id, soul_id, score_data["score"], score_data["passed"], json.dumps(meta)),
                )
            conn.commit()
    except Exception as exc:
        print(f"[eval] DB write failed: {exc}", file=sys.stderr)


def _load_baseline(benchmark_id: str) -> dict[str, Any] | None:
    """Load the saved baseline for a benchmark."""
    baseline_path = BASELINES_DIR / f"{benchmark_id}.json"
    if not baseline_path.exists():
        return None
    try:
        return json.loads(baseline_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_baseline(benchmark_id: str, score_data: dict, duration_ms: float) -> None:
    """Save benchmark results as a new baseline."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    baseline = {
        "benchmark_id": benchmark_id,
        "score": score_data["score"],
        "duration_ms": duration_ms,
        "saved_at": time.time(),
        "pattern_score": score_data["pattern_score"],
        "matches": score_data["matches"],
    }
    (BASELINES_DIR / f"{benchmark_id}.json").write_text(json.dumps(baseline, indent=2))


def _check_regression(current_score: float, baseline: dict) -> dict[str, Any]:
    """Compare against baseline. Returns regression info."""
    baseline_score = baseline.get("score", 1.0)
    delta = baseline_score - current_score
    pct_change = (delta / baseline_score * 100) if baseline_score > 0 else 0
    regressed = delta > 0.05  # 5% threshold
    return {
        "baseline_score": baseline_score,
        "current_score": current_score,
        "delta": round(delta, 4),
        "pct_change": round(pct_change, 2),
        "regressed": regressed,
    }


# --- Drift detection ---

class DriftDetector:
    """Tracks rolling 20-task window per soul_id for drift signals."""

    def __init__(self, window_size: int = 20) -> None:
        self._windows: dict[str, deque] = {}
        self._window_size = window_size

    def record(self, soul_id: str, score: float, passed: bool, duration_ms: float) -> dict[str, Any]:
        if soul_id not in self._windows:
            self._windows[soul_id] = deque(maxlen=self._window_size)
        self._windows[soul_id].append({"score": score, "passed": passed, "duration_ms": duration_ms})
        return self.check(soul_id)

    def check(self, soul_id: str) -> dict[str, Any]:
        window = self._windows.get(soul_id, deque())
        if len(window) < 2:
            return {"drift": False, "reason": "insufficient samples"}

        scores = [r["score"] for r in window]
        pass_rates = [r["passed"] for r in window]
        durations = [r["duration_ms"] for r in window]

        avg_score = sum(scores) / len(scores)
        pass_rate = sum(1 for p in pass_rates if p) / len(pass_rates)
        avg_duration = sum(durations) / len(durations)

        # Check drift signals
        alerts = []
        if pass_rate < 0.95 and len(window) >= 5:
            alerts.append(f"pass-rate {pass_rate:.0%} below 95%")
        if avg_score < 0.4:
            alerts.append(f"avg-score {avg_score:.2f} below 0.4")
        if len(window) >= 5:
            recent = list(window)[-5:]
            recent_scores = [r["score"] for r in recent]
            recent_avg = sum(recent_scores) / len(recent_scores)
            if recent_avg < avg_score * 0.7:
                alerts.append(f"recent scores ({recent_avg:.2f}) < 70% of window avg ({avg_score:.2f})")

        return {
            "drift": len(alerts) > 0,
            "alerts": alerts,
            "sample_count": len(window),
            "avg_score": round(avg_score, 3),
            "pass_rate": round(pass_rate, 3),
            "avg_duration_ms": round(avg_duration, 0),
        }


_drift_detector = DriftDetector()


async def run_benchmarks(benchmark_id: str | None = None, save_baseline: bool = False) -> int:
    specs = _load_benchmarks(benchmark_id)
    if not specs:
        print("[eval] no benchmarks found")
        return 0

    total = 0
    passed_count = 0
    regressions = 0

    for spec in specs:
        bid = spec["benchmark_id"]
        sid = spec["soul_id"]
        prompt = spec["input_prompt"]
        print(f"[eval] running {bid} ({sid})...", flush=True)

        start = time.time()
        try:
            output = await _run_agent(sid, prompt)
        except Exception as exc:
            print(f"[eval]   FAILED (agent error): {exc}", flush=True)
            _drift_detector.record(sid, 0.0, False, 0)
            continue
        duration_ms = (time.time() - start) * 1000

        score_data = _score_output(output, spec)
        status = "PASS" if score_data["passed"] else "FAIL"

        # Baseline comparison
        baseline = _load_baseline(bid)
        regression_info = ""
        if baseline and not save_baseline:
            reg = _check_regression(score_data["score"], baseline)
            if reg["regressed"]:
                regression_info = f" REGRESSION {reg['pct_change']:.1f}%"
                regressions += 1
            else:
                regression_info = f" (baseline={reg['baseline_score']:.3f} delta={reg['delta']:+.3f})"

        print(
            f"[eval]   {status} score={score_data['score']:.3f} "
            f"matches={score_data['matches']}/{score_data['expected_count']} "
            f"violations={score_data['violations']} "
            f"({duration_ms:.0f}ms){regression_info}",
            flush=True,
        )

        _write_eval(sid, bid, score_data, duration_ms)

        # Drift detection
        drift = _drift_detector.record(sid, score_data["score"], score_data["passed"], duration_ms)
        if drift["drift"]:
            print(f"[eval]   DRIFT ALERT ({sid}): {'; '.join(drift['alerts'])}", flush=True)

        if save_baseline:
            _save_baseline(bid, score_data, duration_ms)
            print(f"[eval]   baseline saved", flush=True)

        total += 1
        if score_data["passed"]:
            passed_count += 1

    summary = f"[eval] {passed_count}/{total} benchmarks passed"
    if regressions > 0:
        summary += f", {regressions} regressions"
    print(summary)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="RASA benchmark regression runner")
    parser.add_argument("--benchmark", default=None, help="Run a specific benchmark ID")
    parser.add_argument("--all", action="store_true", help="Run all benchmarks")
    parser.add_argument("--save-baseline", action="store_true", help="Save results as new baseline")
    args = parser.parse_args()

    benchmark_id = args.benchmark if not args.all else None
    count = asyncio.run(run_benchmarks(benchmark_id, save_baseline=args.save_baseline))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
