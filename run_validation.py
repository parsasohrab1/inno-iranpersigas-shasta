#!/usr/bin/env python
"""One-shot validation runner: re-runs every validation suite and writes a single summary.

  python -I run_validation.py <product_dir> [webimg_root] [--lang fa]

Suites (each writes its own JSON under validation/results/):
  1. rule engines        -> rules_validation.json        (pipelines/validate_rules.py)
  2. forecast back-test  -> forecast_backtest.json       (pipelines/backtest_forecast.py)
  3. forecast ensembles  -> forecast_ensemble.json       (pipelines/forecast_ensemble.py)
  4. auto-selection      -> forecast_auto_select.json    (pipelines/forecast_auto_select.py)
  5. vision model        -> vision_validation.json       (pipelines/train_vision.py, needs webimg_root)

The summary (validation/results/summary.json) lists what passed, what failed and what needs external
data, so a reviewer can see the validation state without reading every JSON.
"""
import json
import os
import subprocess
import sys
import time

product = os.path.abspath(sys.argv[1])
webimg = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
lang = "fa" if "--lang" in sys.argv else "en"
pipelines = os.path.join(product, "pipelines")
results = os.path.join(product, "validation", "results")

SUITES = [
    ("rules", ["validate_rules.py", product, "--lang", lang] if lang == "fa" else ["validate_rules.py", product]),
    ("forecast_backtest", ["backtest_forecast.py", product, "--lang", lang] if lang == "fa"
     else ["backtest_forecast.py", product]),
    ("forecast_ensemble", ["forecast_ensemble.py", product]),
    ("forecast_auto_select", ["forecast_auto_select.py", product]),
]
if webimg:
    SUITES.append(("vision", ["train_vision.py", product, webimg, "--lang", lang] if lang == "fa"
                   else ["train_vision.py", product, webimg]))

env = dict(os.environ)
out = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "product": product, "suites": {}}
for name, cmd in SUITES:
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-I", os.path.join(pipelines, cmd[0]), *cmd[1:]],
                          capture_output=True, text=True, timeout=3600, cwd=product, env=env)
    ok = proc.returncode == 0
    out["suites"][name] = {"ok": ok, "seconds": round(time.time() - t0, 1),
                           "results_file": f"validation/results/{name if name != 'rules' else 'rules'}"
                                           f"{'_validation' if name == 'rules' else ''}.json",
                           "stderr_tail": proc.stderr[-800:] if not ok else ""}
    print(f"[{'ok' if ok else 'FAIL'}] {name}  {out['suites'][name]['seconds']}s")
    if not ok:
        print(proc.stderr[-800:])

# ---------------------------------------------------------------- summary of the result files
def load(name):
    p = os.path.join(results, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


rules = load("rules_validation.json")
bt = load("forecast_backtest.json")
vis = load("vision_validation.json")
out["headline"] = {
    "rules_pass": bool(rules and rules.get("pass")),
    "forecast_selected": (bt or {}).get("weekly_national", {}).get("selected"),
    "forecast_policy_equals_best": (bt or {}).get("weekly_national", {}).get("policy_equals_best_on_backtest"),
    "vision_test_accuracy": (vis or {}).get("test_accuracy"),
    "vision_thresholds": (vis or {}).get("thresholds"),
}
out["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
os.makedirs(results, exist_ok=True)
json.dump(out, open(os.path.join(results, "summary.json"), "w"), indent=1, ensure_ascii=False)
print(json.dumps(out["headline"], ensure_ascii=False))
sys.exit(0 if all(s["ok"] for s in out["suites"].values()) else 1)
