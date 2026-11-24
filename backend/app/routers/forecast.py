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
        df = pd.read_csv(WEEKLY_CSV).sort_values("date")
        period = 52
        label = "US weekly national (propane/propylene product supplied)"
        unit = "thousand barrels per day"
        y = df["value"].to_numpy(float)
        dates = pd.to_datetime(df["date"]).dt.date
    else:
        df = pd.read_csv(MONTHLY_CSV)
        m = df[df["region"].str.contains(region.strip(), case=False, regex=False)]
        if series:
            m = m[m["series"] == series]
        if m.empty:
            raise HTTPException(404, f"no regional series matches '{region}'; known: {list(PADDS)}")
        # one series per region (the most complete one) - deterministically the first after sorting
        g = m.sort_values("date").groupby(["series", "region"], sort=True)
        (s_name, r_name), g = next(iter(g))
        period = 12
        label = f"{r_name} monthly ({s_name} product supplied)"
        unit = "thousand barrels per day"
        y = g["value"].to_numpy(float)
        dates = pd.to_datetime(g["date"]).dt.date
    if len(y) < 30:
        raise HTTPException(503, "series too short for the forecasting engine")
    kind, reason = recommended_kind(len(y), period)
    m = ForecastModel(kind, period).fit(y)
    path = m.predict_path(y, horizon)
    lo, hi = m.interval(0.8)
    step = timedelta(days=7) if period == 52 else None
    records = []
    for i, p in enumerate(path, start=1):
        last = dates.iloc[-1]
        d = _add_months(last, i) if step is None else last + i * step
        records.append({"date": d.isoformat(), "demand": round(float(p), 2),
                        "lo80": round(float(p + lo), 2), "hi80": round(float(p + hi), 2)})
    return {"region": region or "US", "series": label, "unit": unit, "period": period, "horizon": horizon,
            "model": {"kind": kind, "reason": reason, "trained_on": len(y), "interval_q80": [lo, hi]},
            "records": records,
            "note": "SRS FR-07 asks for 7-day DAILY regional forecasts; public data support only weekly "
                    "national and monthly regional steps. A daily regional model needs the operator's "
                    "station-level sales data."}
