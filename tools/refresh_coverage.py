"""Run the test suite and write a small coverage snapshot for the dashboard.

    python tools/refresh_coverage.py

The snapshot is what the «آزمون‌ها و پوشش» tab reads. It is a measurement of this
tree, not a synthetic number.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
OUT = os.path.join(ROOT, "validation", "results", "coverage_snapshot.json")
RAW = os.path.join(ROOT, "validation", "results", "coverage_raw.json")


def main() -> int:
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--tb=no",
         "--cov=app", f"--cov-report=json:{RAW}"],
        cwd=BACKEND, env=env, text=True, capture_output=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if not os.path.isfile(RAW):
        print("coverage json was not written", file=sys.stderr)
        return proc.returncode or 1
    raw = json.load(open(RAW, encoding="utf-8"))
    files = []
    for path, meta in sorted(raw.get("files", {}).items()):
        summary = meta.get("summary", {})
        files.append({
            "path": path.replace("\\", "/"),
            "percent": round(summary.get("percent_covered", 0), 1),
            "stmts": summary.get("num_statements", 0),
            "miss": summary.get("missing_lines", 0),
            "covered": summary.get("covered_lines", 0),
        })
    totals = raw.get("totals", {})
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=BACKEND, env=env, text=True, capture_output=True)
    modules: dict[str, int] = {}
    for line in collected.stdout.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        module = line.split("::", 1)[0].replace("\\", "/")
        modules[module] = modules.get(module, 0) + 1
    passed = failed = 0
    for line in proc.stdout.splitlines():
        if " passed" in line:
            parts = line.replace(",", " ").split()
            for i, token in enumerate(parts):
                if token == "passed" and i:
                    passed = int(parts[i - 1])
                if token == "failed" and i:
                    failed = int(parts[i - 1])
    snap = {
        "available": True,
        "passed": passed,
        "failed": failed,
        "percent_covered": round(totals.get("percent_covered", 0), 2),
        "num_statements": totals.get("num_statements", 0),
        "covered_lines": totals.get("covered_lines", 0),
        "missing_lines": totals.get("missing_lines", 0),
        "files": files,
        "test_modules": [{"file": k, "tests": v} for k, v in sorted(modules.items())],
    }
    json.dump(snap, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {OUT} coverage={snap['percent_covered']}% tests={passed} failed={failed}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
