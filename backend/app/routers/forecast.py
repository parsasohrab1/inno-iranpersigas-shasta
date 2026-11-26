"""FR-07 demand forecast endpoint, backed by the validated forecasting engine and the REAL EIA series.

Honest scope note (returned with every answer): the SRS asks for 7-day daily demand per region, but the
only public data available are the weekly US national and the monthly regional product-supplied series
(EIA). The engine therefore returns weekly steps for the national series and monthly steps for the
regional series; a daily regional forecast needs station-level sales data that the operator owns.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.config import EIA_DIR
from ..core.security import current_user
from ..services.forecast import ForecastModel, recommended_kind

router = APIRouter(prefix="/api/forecast", tags=["forecast"])

WEEKLY_CSV = os.path.join(EIA_DIR, "eia_weekly_us_propane_propylene_product_supplied.csv")
MONTHLY_CSV = os.path.join(EIA_DIR, "eia_monthly_padd_product_supplied.csv")
PADDS = ("East Coast (PADD 1)", "Midwest (PADD 2)", "Gulf Coast (PADD 3)", "Rocky Mountain (PADD 4)",
         "West Coast (PADD 5)")

# D-30: read_csv + fit are cached until the source file's mtime changes.
_FRAME_CACHE: dict[str, tuple[float, object]] = {}
_MODEL_CACHE: dict[tuple, ForecastModel] = {}


def _read_csv(path: str) -> pd.DataFrame:
    mtime = os.path.getmtime(path)
    hit = _FRAME_CACHE.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    frame = pd.read_csv(path)
    _FRAME_CACHE[path] = (mtime, frame)
    return frame


def _regional_slice(df: pd.DataFrame, region: str, series: str | None):
    """Pick one regional series. Exact name wins; a unique substring is accepted; ambiguity is 422.

    D-06: when the caller does not name a series, the longest one is used (latest end date breaks ties),
    not the alphabetically first group.
    """
    names = sorted(set(df["region"].astype(str)))
    key = region.strip().lower()
    exact = [r for r in names if r.lower() == key]
    if exact:
        chosen = exact[0]
    else:
        hits = [r for r in names if key in r.lower()]
        if len(hits) == 1:
            chosen = hits[0]
        elif len(hits) > 1:
            raise HTTPException(422, f"region '{region}' matches more than one area: {hits}")
        else:
            raise HTTPException(404, f"no regional series matches '{region}'; known: {list(PADDS)}")
    m = df[df["region"].astype(str) == chosen]
    if series:
        m = m[m["series"] == series]
        if m.empty:
            known = sorted(set(df[df["region"].astype(str) == chosen]["series"].astype(str)))
            raise HTTPException(404, f"series '{series}' is not in {chosen}; known: {known}")
        s_name = series
    else:
        counts = m.groupby("series").size()
        top = int(counts.max())
        cands = [s for s, n in counts.items() if int(n) == top]
        last = m.groupby("series")["date"].max()
        cands.sort(key=lambda s: (str(last.loc[s]), str(s)))
        s_name = cands[-1]
        m = m[m["series"] == s_name]
    return m.sort_values("date"), s_name, chosen


def _fitted(cache_key: tuple, y: np.ndarray, period: int) -> tuple[ForecastModel, str, str]:
    hit = _MODEL_CACHE.get(cache_key)
    if hit is not None:
        return hit
    kind, reason = recommended_kind(len(y), period)
    model = ForecastModel(kind, period).fit(y)
    packed = (model, kind, reason)
    _MODEL_CACHE[cache_key] = packed
    return packed


def _add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    try:
        return d.replace(year=y, month=m)
    except ValueError:
        return d.replace(year=y, month=m, day=28)


@router.get("")
def forecast(region: str | None = None, horizon: int = Query(7, ge=1, le=52),
             series: str | None = None, user: dict = Depends(current_user)):
    if region is None or region.strip().lower() in ("us", "national", "weekly", "us_total"):
        df = _read_csv(WEEKLY_CSV).sort_values("date")
        period = 52
        label = "US weekly national (propane/propylene product supplied)"
        unit = "thousand barrels per day"
        y = df["value"].to_numpy(float)
        dates = pd.to_datetime(df["date"]).dt.date
        cache_key = ("weekly", os.path.getmtime(WEEKLY_CSV), len(y))
    else:
        df = _read_csv(MONTHLY_CSV)
        g, s_name, r_name = _regional_slice(df, region, series)
        period = 12
        label = f"{r_name} monthly ({s_name} product supplied)"
        unit = "thousand barrels per day"
        y = g["value"].to_numpy(float)
        dates = pd.to_datetime(g["date"]).dt.date
        cache_key = ("monthly", os.path.getmtime(MONTHLY_CSV), r_name, s_name, len(y))
    if len(y) < 30:
        raise HTTPException(503, "series too short for the forecasting engine")
    model, kind, reason = _fitted(cache_key, y, period)
    path = model.predict_path(y, horizon)
    step = timedelta(days=7) if period == 52 else None
    records = []
    for i, p in enumerate(path, start=1):
        lo, hi = model.interval(0.8, horizon=i)
        last = dates.iloc[-1]
        d = _add_months(last, i) if step is None else last + i * step
        lo_v = max(0.0, float(p + lo))
        hi_v = max(lo_v, float(p + hi))
        records.append({"date": d.isoformat(), "demand": round(float(p), 2),
                        "lo80": round(lo_v, 2), "hi80": round(hi_v, 2)})
    lo1, hi1 = model.interval(0.8, horizon=1)
    return {"region": region or "US", "series": label, "unit": unit, "period": period, "horizon": horizon,
            "model": {"kind": kind, "reason": reason, "trained_on": len(y), "interval_q80": [lo1, hi1]},
            "records": records,
            "note": "SRS FR-07 asks for 7-day DAILY regional forecasts; public data support only weekly "
                    "national and monthly regional steps. A daily regional model needs the operator's "
                    "station-level sales data. The 80% band is the one-step residual quantile scaled by "
                    "sqrt(horizon); multi-step coverage is not yet calibrated on a per-horizon back-test."}
