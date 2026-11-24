#!/usr/bin/env python
"""Is a forecast combination more robust than picking one method? (FR-07, real EIA series)

  python -I forecast_ensemble.py <product_dir>

For the weekly national series (260 origins) and the 10 regional monthly series (60 origins) several
fixed combinations are compared with every single method on the same walk-forward predictions.
Writes validation/results/forecast_ensemble.json.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(sys.argv[1], "backend"))
from app.services.forecast import KINDS, metrics, walk_forward  # noqa: E402

product = sys.argv[1]
COMBOS = {
    "mean_all_6": list(KINDS),
    "median_all_6": list(KINDS),
    "mean_seasonal_drift_ridge": ["seasonal_naive", "seasonal_drift", "ridge"],
    "mean_ma14_seasonal_ridge": ["moving_average_14d", "seasonal_naive", "ridge"],
    "mean_ridge_seasonal_drift": ["ridge", "seasonal_drift"],
}


def paths(y, period, n_origins, refit):
    return {k: walk_forward(np.asarray(y, float), k, period, n_origins, 1, refit_every=refit)[0][:, 0] for k in KINDS}


def evaluate(y, period, n_origins, refit):
    p = paths(y, period, n_origins, refit)
    y = np.asarray(y, float)
    actual = y[len(y) - n_origins:]
    out = {k: metrics(actual, v) for k, v in p.items()}
    for name, members in COMBOS.items():
        M = np.column_stack([p[m] for m in members])
        comb = np.median(M, axis=1) if name.startswith("median") else M.mean(axis=1)
        out[name] = metrics(actual, comb)
    return out


series = []
wk = pd.read_csv(os.path.join(product, "data/eia/eia_weekly_us_propane_propylene_product_supplied.csv"))
series.append(("US weekly national", wk.value.to_numpy(float), 52, 260, 4))
mo = pd.read_csv(os.path.join(product, "data/eia/eia_monthly_padd_product_supplied.csv"))
for (s, reg), g in mo.groupby(["series", "region"], sort=True):
    if len(g) >= 120:
        series.append((f"{s} | {reg}", g.value.to_numpy(float), 12, 60, 6))
names = list(KINDS) + list(COMBOS)
regional = {n: [] for n in names}
wins = {n: 0 for n in names}
rows = []
weekly = None
for title, y, period, n_origins, refit in series:
    r = evaluate(y, period, n_origins, refit)
    best = min(names, key=lambda n: r[n]["rmse"])
    wins[best] += 1
    for n in names:
        regional[n].append(r[n]["rmse"])
    rows.append({"series": title, "period": period, "n_origins": n_origins,
                 "rmse": {n: round(r[n]["rmse"], 3) for n in names}, "wape": {n: round(r[n]["wape"], 2) for n in names},
                 "best": best})
    if weekly is None:
        weekly = r
regional = {n: regional[n][1:] for n in names}          # drop the weekly series from the regional summary
print("weekly national (260 origins): RMSE / WAPE %")
for n in sorted(names, key=lambda n: weekly[n]["rmse"]):
    print(f"  {n:28s} RMSE={weekly[n]['rmse']:7.1f}  WAPE={weekly[n]['wape']:6.2f} %")
print("\n10 regional monthly series (60 origins each): mean RMSE, mean relative RMSE vs the best method of each series, wins")
rel = {n: [regional[n][i] / min(regional[m][i] for m in names) for i in range(len(regional[n]))] for n in names}
for n in sorted(names, key=lambda n: np.mean(rel[n])):
    worse_mvp = sum(1 for i in range(10) if regional[n][i] > regional["moving_average_14d"][i])
    print(f"  {n:28s} meanRMSE={np.mean(regional[n]):7.2f}  mean rel. RMSE={100 * (np.mean(rel[n]) - 1):6.2f} %  "
          f"max rel. RMSE={100 * (max(rel[n]) - 1):6.2f} %  wins={wins[n] - (1 if rows[0]['best'] == n else 0)}  worse than MA14 in {worse_mvp} series")
json.dump({"combos": COMBOS, "rows": rows, "regional_mean_rmse": {n: float(np.mean(regional[n])) for n in names},
           "regional_mean_relative_rmse_pct": {n: 100 * float(np.mean(rel[n]) - 1) for n in names},
           "regional_max_relative_rmse_pct": {n: 100 * float(max(rel[n]) - 1) for n in names},
           "regional_wins": {n: wins[n] - (1 if rows[0]["best"] == n else 0) for n in names}},
          open(os.path.join(product, "validation/results/forecast_ensemble.json"), "w"), indent=1)

# ---- how much weekly history does the learned model need before it beats the combination? ---------------
print("\nweekly national: ridge alone vs the seasonal/ridge combination as a function of the history length")
y = wk.value.to_numpy(float)
sens = []
for n_hist in (120, 200, 300, 500, 800, len(y)):
    origins = min(52, n_hist // 3)
    r = evaluate(y[:n_hist], 52, origins, 4)
    sens.append({"history_length": int(n_hist), "n_origins": int(origins), "ridge_rmse": r["ridge"]["rmse"],
                 "ensemble_rmse": r["mean_seasonal_drift_ridge"]["rmse"], "ma14_rmse": r["moving_average_14d"]["rmse"],
                 "best_seasonal_rmse": min(r["seasonal_naive"]["rmse"], r["seasonal_drift"]["rmse"])})
    print(f"  history {n_hist:5d} ({origins:3d} origins): ridge {r['ridge']['rmse']:7.1f} | combination "
          f"{r['mean_seasonal_drift_ridge']['rmse']:7.1f} | MA14 {r['moving_average_14d']['rmse']:7.1f} | "
          f"best seasonal {sens[-1]['best_seasonal_rmse']:7.1f}")
res = json.load(open(os.path.join(product, "validation/results/forecast_ensemble.json")))
res["weekly_history_sensitivity"] = sens
json.dump(res, open(os.path.join(product, "validation/results/forecast_ensemble.json"), "w"), indent=1)
