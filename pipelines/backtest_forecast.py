#!/usr/bin/env python
"""FR-07 demand forecast: walk-forward back-test on real public data (U.S. Energy Information Administration).

  python -I backtest_forecast.py <product_dir> [--lang fa|en]

Weekly national series  : 260 rolling origins (one 1..4-week-ahead forecast per week), season length 52.
Regional monthly series : 5 PADD regions x 2 series, 60 rolling origins each, season length 12.
At every origin only the data available up to that origin is used.

Writes validation/results/forecast_backtest.json, validation/figures/forecast_*.png and
backend/ml/forecast_model.json (the selected model re-trained on the full weekly series).
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

WEEKLY = "data/eia/eia_weekly_us_propane_propylene_product_supplied.csv"
MONTHLY = "data/eia/eia_monthly_padd_product_supplied.csv"
N_ORIGINS_WEEKLY, H_WEEKLY, N_ORIGINS_MONTHLY = 260, 4, 60
LABEL = {
    "naive": {"fa": "ساده (آخرین مقدار)", "en": "naive (last value)"},
    "moving_average_14d": {"fa": "میانگین متحرک ۱۴ (روش MVP)", "en": "moving average 14 (MVP)"},
    "seasonal_naive": {"fa": "فصلی ساده (یک فصل قبل)", "en": "seasonal naive"},
    "seasonal_drift": {"fa": "فصلی با روند سطح", "en": "seasonal + level drift"},
    "ridge": {"fa": "رگرسیون هارمونیک-با تأخیر", "en": "harmonic-lag regression"},
    "gbm": {"fa": "بوستینگ گرادیانی (GBM)", "en": "gradient boosting"},
    "ensemble_seasonal_ridge": {"fa": "ترکیب فصلی + رگرسیون", "en": "seasonal + ridge combination"},
}
BASELINES = ("naive", "moving_average_14d", "seasonal_naive", "seasonal_drift")
CANDIDATES = ("ridge", "gbm")
SELECTION_RULE = (
    "The method is fixed by the length of the available history (recommended_kind), from the walk-forward evidence "
    "on the real series: fewer than 14 observations -> naive; 600 or more -> ridge alone; in between -> mean of "
    "seasonal naive, seasonal drift and ridge. The weekly national series has more than 600 points, so ridge is "
    "the method of its artefact; the back-test then checks whether that method is also the most accurate one on "
    "the 260 origins (policy_equals_best_on_backtest).")


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_series(y, period, n_origins, horizon, kinds, refit_gbm):
    """Walk-forward forecasts of every model on one series."""
    from app.services.forecast import walk_forward, metrics

    y = np.asarray(y, float)
    out = {}
    for kind in kinds:
        t0 = time.time()
        paths, origins, lo, hi = walk_forward(y, kind, period, n_origins, horizon, refit_every=refit_gbm)
        entry = {"fit_seconds": round(time.time() - t0, 1)}
        for h in range(1, horizon + 1):
            entry[f"h{h}"] = metrics(y[origins[h - 1:]], paths[h - 1:, h - 1])
        entry["coverage80_h1"] = float(np.mean((y[origins] >= lo) & (y[origins] <= hi)))
        out[kind] = {"entry": entry, "pred_h1": paths[:, 0], "lo": lo, "hi": hi, "origins": origins}
    return out


def weekly(product, plt, T, lang, results_dir, fig_dir):
    from app.services.forecast import KINDS, ForecastModel, block_bootstrap_skill, metrics, recommended_kind
    from plotutil import save

    path = os.path.join(product, WEEKLY)
    df = pd.read_csv(path, parse_dates=["date"])
    y = df["value"].to_numpy(float)
    dates = df["date"]
    res = run_series(y, 52, N_ORIGINS_WEEKLY, H_WEEKLY, KINDS, refit_gbm=4)
    origins = res["naive"]["origins"]
    actual = y[origins]
    prev = y[origins - 1]

    models, skill_mvp, skill_seasonal = {}, {}, {}
    for kind in KINDS:
        e = dict(res[kind]["entry"])
        p = res[kind]["pred_h1"]
        e["direction_accuracy_h1"] = float(np.mean(np.sign(p - prev) == np.sign(actual - prev)))
        for name, mask in (("heating_oct_mar", dates[origins].dt.month.isin([10, 11, 12, 1, 2, 3]).to_numpy()),
                           ("warm_apr_sep", ~dates[origins].dt.month.isin([10, 11, 12, 1, 2, 3]).to_numpy())):
            e[name] = metrics(actual[mask], p[mask])
        models[kind] = e
        skill_mvp[kind] = block_bootstrap_skill(actual, p, res["moving_average_14d"]["pred_h1"])
        skill_seasonal[kind] = block_bootstrap_skill(actual, p, res["seasonal_naive"]["pred_h1"])

    best_cand = min(CANDIDATES, key=lambda k: models[k]["h1"]["rmse"])
    best_base = min(BASELINES, key=lambda k: models[k]["h1"]["rmse"])
    best_overall = min(KINDS, key=lambda k: models[k]["h1"]["rmse"])
    selected, policy_reason = recommended_kind(len(y))          # fixed policy, not a per-series pick

    final = ForecastModel(selected, 52).fit(y)
    art = final.to_dict()
    art.update(
        model_version=f"forecast-{selected}-w52-{dates.iloc[-1]:%Y%m%d}",
        series="EIA WPRUP_NUS_2, Weekly U.S. Product Supplied of Propane and Propylene",
        unit="thousand barrels per day", frequency="weekly", trained_on=int(len(y)),
        first_date=dates.iloc[0].strftime("%Y-%m-%d"), last_date=dates.iloc[-1].strftime("%Y-%m-%d"),
        data_file=WEEKLY, data_sha256=sha256(path),
        trained_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        backtest_h1=models[selected]["h1"], backtest_coverage80=models[selected]["coverage80_h1"])
    ml_dir = os.path.join(product, "backend", "ml")
    os.makedirs(ml_dir, exist_ok=True)
    with open(os.path.join(ml_dir, "forecast_model.json"), "w", encoding="utf-8") as f:
        json.dump(art, f, ensure_ascii=False, indent=1)

    out = {"data_file": WEEKLY, "data_sha256": sha256(path), "n_points": int(len(y)),
           "first_date": art["first_date"], "last_date": art["last_date"],
           "origin_first": dates.iloc[origins[0]].strftime("%Y-%m-%d"),
           "origin_last": dates.iloc[origins[-1]].strftime("%Y-%m-%d"),
           "n_origins": int(len(origins)), "horizons_weeks": list(range(1, H_WEEKLY + 1)), "period_weeks": 52,
           "models": models, "skill_vs_mvp_moving_average": skill_mvp,
           "skill_vs_seasonal_naive": skill_seasonal, "selected": selected, "policy_reason": policy_reason,
           "best_on_backtest": best_overall, "policy_equals_best_on_backtest": selected == best_overall,
           "best_candidate": best_cand, "best_baseline": best_base, "candidates": list(CANDIDATES),
           "artifact": {k: art[k] for k in ("model_version", "kind", "trained_on", "first_date", "last_date",
                                             "trained_at", "data_sha256")}}

    # ---------------------------------------------------------------- figures
    lw = {kind: LABEL[kind][lang] for kind in KINDS}
    fig, ax = plt.subplots(figsize=(10, 4), constrained_layout=True)
    ax.plot(dates, y, color="#2563eb", lw=0.9)
    ax.axvspan(dates.iloc[origins[0]], dates.iloc[-1], color="#f59e0b", alpha=0.15)
    ax.set_title(T("دادهٔ واقعی آموزش/آزمون پیش‌بینی تقاضا: عرضهٔ هفتگی پروپان و پروپیلن آمریکا (EIA)"))
    ax.set_ylabel(T("هزار بشکه در روز"))
    ax.annotate(T(f"پنجرهٔ {N_ORIGINS_WEEKLY} سنجهٔ متحرک"), xy=(dates.iloc[origins[0] + 10], y.max() * 0.95), fontsize=9)
    ax.grid(alpha=0.3)
    save(fig, os.path.join(fig_dir, "forecast_series.png"))

    w = 130
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True, constrained_layout=True,
                                 gridspec_kw={"height_ratios": [3, 1.3]})
    a1.plot(dates.iloc[origins[-w:]], actual[-w:], color="#111827", lw=1.6, label=T("مقدار واقعی"))
    a1.fill_between(dates.iloc[origins[-w:]], res[selected]["lo"][-w:], res[selected]["hi"][-w:],
                    color="#2563eb", alpha=0.15, label=T("بازهٔ ۸۰٪ مدل انتخاب‌شده"))
    a1.plot(dates.iloc[origins[-w:]], res[selected]["pred_h1"][-w:], color="#2563eb", lw=1.4,
            label=lw[selected] + (T(" (انتخاب‌شده)") if lang == "fa" else " (selected)"))
    a1.plot(dates.iloc[origins[-w:]], res["moving_average_14d"]["pred_h1"][-w:], color="#f97316", lw=1.1,
            ls="--", label=lw["moving_average_14d"])
    a1.set_title(T(f"پیش‌بینی یک هفته بعد در {w} هفتهٔ آخر (هر نقطه فقط با داده‌های قبل از آن)"))
    a1.set_ylabel(T("هزار بشکه در روز"))
    a1.legend(loc="upper left", bbox_to_anchor=(0, -0.02), fontsize=8, ncol=4, frameon=False)
    a1.grid(alpha=0.3)
    a2.bar(dates.iloc[origins[-w:]], (actual - res[selected]["pred_h1"])[-w:], width=5, color="#2563eb",
           alpha=0.8, label=lw[selected])
    a2.bar(dates.iloc[origins[-w:]], (actual - res["moving_average_14d"]["pred_h1"])[-w:], width=5,
           color="#f97316", alpha=0.45, label=lw["moving_average_14d"])
    a2.axhline(0, color="black", lw=0.6)
    a2.set_ylabel(T("خطا (واقعی − پیش‌بینی)"))
    a2.grid(alpha=0.3)
    save(fig, os.path.join(fig_dir, "forecast_backtest.png"))

    fig, ax = plt.subplots(figsize=(9.6, 4.6), constrained_layout=True)
    order = [k for k in KINDS]                 # top to bottom: the same order as KINDS, drawn bottom-up
    yv = np.arange(len(order))[::-1]
    bh = 0.2
    shades = ["#93c5fd", "#60a5fa", "#3b82f6", "#1d4ed8"]
    for i, h in enumerate(range(1, H_WEEKLY + 1)):
        ax.barh(yv + (1.5 - i) * bh, [models[k][f"h{h}"]["rmse"] for k in order], bh, color=shades[i],
                label=T(f"{h} هفته بعد") if lang == "fa" else f"{h} week(s) ahead")
    ax.set_yticks(yv, [lw[k] for k in order], fontsize=9)
    ax.set_xlabel(T("RMSE (هزار بشکه در روز)"))
    ax.set_title(T(f"خطای پیش‌بینی مدل‌ها روی دادهٔ واقعی EIA ({N_ORIGINS_WEEKLY} سنجهٔ متحرک)"))
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="lower right")
    ax.grid(axis="x", alpha=0.3)
    save(fig, os.path.join(fig_dir, "forecast_models.png"))
    return out


def regional(product, plt, T, lang, fig_dir):
    from app.services.forecast import KINDS
    from plotutil import save

    path = os.path.join(product, MONTHLY)
    df = pd.read_csv(path, parse_dates=["date"])
    per, keys = {}, []
    for (series, region), g in df.groupby(["series", "region"], sort=True):
        y = g["value"].to_numpy(float)
        if len(y) < 120:
            continue
        keys.append((series, region))
        res = run_series(y, 12, N_ORIGINS_MONTHLY, 2, KINDS, refit_gbm=6)
        for kind in KINDS:
            per.setdefault(kind, []).append({"series": series, "region": region, "n": int(len(y)),
                                             "first_date": g["date"].iloc[0].strftime("%Y-%m-%d"),
                                             "last_date": g["date"].iloc[-1].strftime("%Y-%m-%d"),
                                             "h1": res[kind]["entry"]["h1"], "h2": res[kind]["entry"]["h2"],
                                             "coverage80_h1": res[kind]["entry"]["coverage80_h1"]})
    summary = {}
    best_per_series = [min(per[k][i]["h1"]["rmse"] for k in KINDS) for i in range(len(keys))]
    for kind in KINDS:
        h1 = [r["h1"] for r in per[kind]]
        rel = [per[kind][i]["h1"]["rmse"] / best_per_series[i] - 1 for i in range(len(keys))]
        summary[kind] = {"series_count": len(h1),
                         "mean_rmse_h1": float(np.mean([m["rmse"] for m in h1])),
                         "mean_mape_h1": float(np.mean([m["mape"] for m in h1])),
                         "mean_wape_h1": float(np.mean([m["wape"] for m in h1])),
                         "mean_mae_h1": float(np.mean([m["mae"] for m in h1])),
                         "mean_coverage80_h1": float(np.mean([r["coverage80_h1"] for r in per[kind]])),
                         "mean_relative_rmse_vs_best_pct": 100 * float(np.mean(rel)),
                         "max_relative_rmse_vs_best_pct": 100 * float(max(rel))}
    for kind in KINDS:   # number of regional series in which each model is the most accurate (h = 1)
        summary[kind]["series_won_h1"] = int(sum(
            1 for i in range(len(keys))
            if per[kind][i]["h1"]["rmse"] == min(per[k][i]["h1"]["rmse"] for k in KINDS)))

    fig, ax = plt.subplots(figsize=(9.6, 4.4), constrained_layout=True)
    from app.services.forecast import ENSEMBLE
    show = ("moving_average_14d", "seasonal_naive", "ridge", ENSEMBLE)
    shades = ["#f97316", "#10b981", "#6366f1", "#111827"]
    x = np.arange(len(keys))
    bw = 0.2
    for i, kind in enumerate(show):
        ax.bar(x + (1.5 - i) * bw, [r["h1"]["mape"] for r in per[kind]], bw, color=shades[i],
               label=LABEL[kind][lang])
    ax.set_xticks(x, [f"{r['region'].split(' (')[0]}\n{r['series']}" for r in per["naive"]], fontsize=7.5)
    ax.set_ylabel(T("MAPE ماه بعد (درصد)"))
    ax.set_title(T(f"پیش‌بینی ماهانهٔ منطقه‌ای روی دادهٔ واقعی EIA ({N_ORIGINS_MONTHLY} سنجهٔ متحرک در هر سری)"))
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    save(fig, os.path.join(fig_dir, "forecast_regional.png"))
    return {"data_file": MONTHLY, "data_sha256": sha256(path), "n_origins": N_ORIGINS_MONTHLY, "period_months": 12,
            "series": [{"series": s, "region": r} for s, r in keys], "summary": summary, "per_series": per}


def main():
    product = sys.argv[1]
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "fa"
    sys.path.insert(0, os.path.join(product, "backend"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from plotutil import save, setup

    plt, T = setup(lang)
    results_dir = os.path.join(product, "validation", "results")
    fig_dir = os.path.join(product, "validation", "figures")
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(fig_dir, exist_ok=True)

    t0 = time.time()
    w = weekly(product, plt, T, lang, results_dir, fig_dir)
    print(f"[weekly] done in {time.time() - t0:.0f}s", flush=True)
    r = regional(product, plt, T, lang, fig_dir)
    out = {"module": "FR-07 demand forecast", "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "method": "walk-forward (rolling-origin) evaluation; every forecast uses only data available at its origin",
           "selection_rule": SELECTION_RULE, "weekly_national": w, "monthly_regional": r}
    with open(os.path.join(results_dir, "forecast_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\n1-week-ahead, weekly national series (lower is better):")
    for k, e in sorted(w["models"].items(), key=lambda kv: kv[1]["h1"]["rmse"]):
        print(f"  {k:20s} MAE={e['h1']['mae']:7.1f} RMSE={e['h1']['rmse']:7.1f} MAPE={e['h1']['mape']:5.2f}% "
              f"cov80={e['coverage80_h1']:.2f} dir={e['direction_accuracy_h1']:.2f} "
              f"skill_vs_MVP={w['skill_vs_mvp_moving_average'][k]['skill']:+.3f} "
              f"CI={w['skill_vs_mvp_moving_average'][k]['ci95']}")
    print("selected:", w["selected"], "| best candidate:", w["best_candidate"], "| best baseline:", w["best_baseline"])
    print("4-week-ahead RMSE:", {k: round(v["h4"]["rmse"], 1) for k, v in w["models"].items()})
    print(f"\n1-month-ahead, 10 regional monthly series (mean over series; relative RMSE = against the best "
          f"method of each series):")
    for k, e in sorted(r["summary"].items(), key=lambda kv: kv[1]["mean_relative_rmse_vs_best_pct"]):
        print(f"  {k:26s} RMSE={e['mean_rmse_h1']:6.1f} MAPE={e['mean_mape_h1']:5.2f}% WAPE={e['mean_wape_h1']:5.2f}% "
              f"cov80={e['mean_coverage80_h1']:.2f} rel.RMSE={e['mean_relative_rmse_vs_best_pct']:+5.1f} % "
              f"(max {e['max_relative_rmse_vs_best_pct']:+5.1f} %) won={e['series_won_h1']}")
    print(f"policy: {w['selected']} | best on back-test: {w['best_on_backtest']} | equal: {w['policy_equals_best_on_backtest']}")
    print(f"[ok] results -> {results_dir}/forecast_backtest.json  total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
