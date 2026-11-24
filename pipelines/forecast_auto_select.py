#!/usr/bin/env python
"""Auto-selection check: does a per-series internal walk-forward choose the method that is really best?

For every series (weekly national, 10 regional monthly) the candidate methods are compared on an inner
window (the first 70 % of the evaluation origins) and the winner is then evaluated on the outer window
(the remaining 30 %). Prints the chosen method, the truly best method on the outer window and the
RMSE of the chosen one on that outer window.

  python -I forecast_auto_select.py <product_dir>
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(sys.argv[1], "backend"))
from app.services.forecast import KINDS, ForecastModel, metrics, walk_forward  # noqa: E402

product = sys.argv[1]


def table(y, period, n_origins, horizon, refit_gbm):
    out = {}
    for kind in KINDS:
        paths, origins, _, _ = walk_forward(np.asarray(y, float), kind, period, n_origins, horizon, refit_every=refit_gbm)
        out[kind] = (paths[:, 0], y[origins])
    return out


ORDER = ["naive", "moving_average_14d", "seasonal_naive", "seasonal_drift", "ridge", "gbm"]
TOLS = (0.0, 0.05, 0.10, 0.20)
ALL = ORDER + [k for k in KINDS if k not in ORDER]      # parsimony order; combinations last


def choose(inner, tol):
    """Simplest method (in ALL) whose inner-window RMSE is within `tol` of the best inner RMSE."""
    best = min(inner.values())
    if not (best > 0) or best != best:                      # constant or degenerate series
        return min(ALL, key=lambda k: inner[k])
    return next(k for k in ALL if inner[k] <= best * (1 + tol))


def auto(y, period, n_origins, horizon, refit_gbm, inner=0.7):
    t = table(y, period, n_origins, horizon, refit_gbm)
    cut = int(n_origins * inner)
    r = {"outer_rmse": {k: metrics(t[k][1][cut:], t[k][0][cut:])["rmse"] for k in KINDS},
         "inner_rmse": {k: metrics(t[k][1][:cut], t[k][0][:cut])["rmse"] for k in KINDS}}
    r["best_outer"] = min(KINDS, key=lambda k: r["outer_rmse"][k])
    r["chosen_by_tolerance"] = {str(tol): choose(r["inner_rmse"], tol) for tol in TOLS}
    r["chosen"] = r["chosen_by_tolerance"]["0.0"]
    return r


rows = []
wk = pd.read_csv(os.path.join(product, "data/eia/eia_weekly_us_propane_propylene_product_supplied.csv"))
r = auto(wk.value.to_numpy(float), 52, 260, 1, 4)
rows.append({"series": "US weekly national", **r})
mo = pd.read_csv(os.path.join(product, "data/eia/eia_monthly_padd_product_supplied.csv"))
for (s, reg), g in mo.groupby(["series", "region"], sort=True):
    if len(g) < 120:
        continue
    rows.append({"series": f"{s} | {reg}", **auto(g.value.to_numpy(float), 12, 60, 1, 6)})

print(f"{'series':55s} {'chosen':20s} {'best(outer)':20s} {'RMSE chosen':>12s} {'RMSE best':>10s} {'RMSE MA14':>10s}")
hit = 0
for r in rows:
    hit += r["chosen"] == r["best_outer"]
    print(f"{r['series'][:55]:55s} {r['chosen']:20s} {r['best_outer']:20s} {r['outer_rmse'][r['chosen']]:12.1f} "
          f"{r['outer_rmse'][r['best_outer']]:10.1f} {r['outer_rmse']['moving_average_14d']:10.1f}")
print(f"\nchosen == best on outer window: {hit}/{len(rows)}")
policy = {}
for tol in TOLS:
    ch = [r["chosen_by_tolerance"][str(tol)] for r in rows]
    regret = [r["outer_rmse"][c] / r["outer_rmse"][r["best_outer"]] - 1 for r, c in zip(rows, ch)]
    vs_mvp = [1 - r["outer_rmse"][c] / r["outer_rmse"]["moving_average_14d"] for r, c in zip(rows, ch)]
    vs_seasonal = [1 - r["outer_rmse"][c] / min(r["outer_rmse"]["seasonal_naive"], r["outer_rmse"]["seasonal_drift"])
                   for r, c in zip(rows, ch)]
    policy[str(tol)] = {"n_equal_best": int(sum(c == r["best_outer"] for r, c in zip(rows, ch))),
                        "mean_regret_pct": 100 * float(np.mean(regret)), "max_regret_pct": 100 * max(regret),
                        "mean_reduction_vs_mvp_pct": 100 * float(np.mean(vs_mvp)),
                        "series_worse_than_mvp": int(sum(v < 0 for v in vs_mvp)),
                        "series_worse_than_best_seasonal": int(sum(v < 0 for v in vs_seasonal)),
                        "mean_reduction_vs_best_seasonal_pct": 100 * float(np.mean(vs_seasonal))}
    print(f"tolerance {tol:.2f}: equal to best {policy[str(tol)]['n_equal_best']}/{len(rows)}, mean regret "
          f"{policy[str(tol)]['mean_regret_pct']:.1f} %, max {policy[str(tol)]['max_regret_pct']:.1f} %, mean vs MVP "
          f"{policy[str(tol)]['mean_reduction_vs_mvp_pct']:+.1f} %, worse than MVP in {policy[str(tol)]['series_worse_than_mvp']} series, "
          f"worse than the best seasonal baseline in {policy[str(tol)]['series_worse_than_best_seasonal']} series")
regret = [r["outer_rmse"][r["chosen"]] / r["outer_rmse"][r["best_outer"]] - 1 for r in rows]
out = {"inner_window_share": 0.7, "rows": rows, "chosen_equals_best": hit, "n_series": len(rows),
       "mean_rmse_regret_pct": 100 * float(np.mean(regret)), "rmse_reduction_vs_mvp_pct": [100 * v for v in vs_mvp],
       "parsimony_policy": policy}
os.makedirs(os.path.join(product, "validation/results"), exist_ok=True)
json.dump(out, open(os.path.join(product, "validation/results/forecast_auto_select.json"), "w"), indent=1)
