"""Deterministic synthetic operations for the LCIS dashboard.

Volumes (inspections, fleet, fillings, regional demand) are generated here because the
readiness letter needs an operational picture and public data does not include Iran
station sales or a Persia Gas cylinder register. Every inspection, lifecycle and filling
row is then classified by the same rule engines the API uses. Demand paths are produced
by the same forecasting code. The seed is fixed, so the picture does not drift between
requests. Measured accuracy stays in the validation files and is not invented here.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .filling import assess_filling
from .forecast import ForecastModel, recommended_kind
from .lifecycle import assess_inspection, assess_lifecycle

SEED = 20261128
AS_OF = date(2026, 11, 28)
REGIONS = ("تهران", "اصفهان", "فارس", "خراسان", "آذربایجان")
STATIONS = ("ST-تهران-۱", "ST-تهران-۲", "ST-اصفهان-۱", "ST-فارس-۱", "ST-خراسان-۱", "ST-آذربایجان-۱")
# Weights sum to 1000. Codes are the ones the inspection rule actually knows.
DEFECT_WEIGHTS = (
    ((), 700),
    (("rust",), 120),
    (("scratch",), 60),
    (("label_missing",), 40),
    (("ring_missing",), 30),
    (("deep_dent",), 20),
    (("valve_damage",), 20),
    (("crack",), 10),
)
DEFECT_FA = {
    "rust": "زنگ‌زدگی",
    "scratch": "خراش",
    "label_missing": "برچسب ناخوانا",
    "ring_missing": "نبود حلقه",
    "deep_dent": "فرورفتگی عمیق",
    "valve_damage": "آسیب شیر",
    "crack": "ترک",
    "none": "بدون عیب",
}
VERDICT_FA = {"pass": "قبول", "review": "بازبینی", "reject": "رد"}
STATUS_FA = {
    "in_service": "در سرویس",
    "due_for_test": "سررسید آزمون",
    "overdue_for_test": "آزمون معوق",
    "suspend": "تعلیق",
    "retire": "خروج از چرخه",
}
ANOMALY_FA = {
    "ok": "سالم",
    "short_fill": "کم‌پری",
    "overfill": "پرپری",
    "leak": "نشتی",
    "tare_loss": "افت تار",
    "tare_out_of_tolerance": "تار خارج از رواداری",
}


def _pick(rng: np.random.Generator, table):
    labels, weights = zip(*table)
    return labels[int(rng.choice(len(labels), p=np.array(weights) / sum(weights)))]


def _inspections(rng: np.random.Generator) -> dict:
    by_verdict: dict[str, int] = {}
    by_defect: dict[str, int] = {}
    by_region: dict[str, dict[str, int]] = {r: {} for r in REGIONS}
    daily = []
    n = 0
    for offset in range(6, -1, -1):
        day = AS_OF - timedelta(days=offset)
        day_counts = {k: 0 for k in VERDICT_FA}
        for region in REGIONS:
            for _ in range(16):  # 5 regions x 16 = 80 inspections a day
                defects = list(_pick(rng, DEFECT_WEIGHTS))
                verdict = assess_inspection(defects)
                key = defects[0] if defects else "none"
                by_defect[key] = by_defect.get(key, 0) + 1
                by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
                by_region[region][verdict] = by_region[region].get(verdict, 0) + 1
                day_counts[verdict] += 1
                n += 1
        daily.append({"date": day.isoformat(), **day_counts, "n": sum(day_counts.values())})
    return {
        "n": n,
        "days": 7,
        "per_day": 80,
        "by_verdict": [{"code": k, "label": VERDICT_FA[k], "n": by_verdict.get(k, 0)} for k in VERDICT_FA],
        "by_defect": [{"code": k, "label": DEFECT_FA.get(k, k), "n": v}
                      for k, v in sorted(by_defect.items(), key=lambda kv: -kv[1])],
        "by_region": [{"region": r, **{k: by_region[r].get(k, 0) for k in VERDICT_FA}} for r in REGIONS],
        "daily": daily,
    }


def _fleet(rng: np.random.Generator) -> dict:
    by_status: dict[str, int] = {}
    by_region: dict[str, dict[str, int]] = {r: {} for r in REGIONS}
    priority = {"high": 0, "medium": 0, "low": 0}
    n = 360
    for i in range(n):
        region = REGIONS[i % len(REGIONS)]
        made = date(2008 + (i % 16), (i % 12) + 1, (i % 27) + 1)
        years_since_test = int(rng.integers(0, 8))
        tested = date(AS_OF.year - years_since_test, made.month, min(made.day, 28))
        defects = list(_pick(rng, DEFECT_WEIGHTS))
        verdict = assess_inspection(defects)
        severity = {"reject": "critical", "review": "minor", "pass": "none"}[verdict]
        cyl = {
            "manufacture_date": made.isoformat(),
            "last_hydro_test": None if rng.random() < 0.06 else tested.isoformat(),
            "fill_count": int(rng.integers(5, 900)),
            "fail_count": 1 if verdict == "reject" else 0,
            "last_inspection_date": AS_OF.isoformat(),
            "last_inspection_result": verdict,
            "last_inspection_severity": severity,
        }
        if verdict == "reject" and rng.random() < 0.25:
            cyl["requalification_date"] = (AS_OF + timedelta(days=3)).isoformat()
        assessed = assess_lifecycle(cyl, today=AS_OF)
        status = assessed["status"]
        by_status[status] = by_status.get(status, 0) + 1
        by_region[region][status] = by_region[region].get(status, 0) + 1
        priority[assessed["priority_level"]] += 1
    order = ("in_service", "due_for_test", "overdue_for_test", "suspend", "retire")
    return {
        "n": n,
        "by_status": [{"code": k, "label": STATUS_FA.get(k, k), "n": by_status.get(k, 0)} for k in order
                      if by_status.get(k, 0)],
        "by_priority": [{"code": k, "label": {"high": "بالا", "medium": "متوسط", "low": "پایین"}[k], "n": priority[k]}
                        for k in ("high", "medium", "low")],
        "by_region": [{"region": r, **{k: by_region[r].get(k, 0) for k in order}} for r in REGIONS],
        "status_keys": [{"code": k, "label": STATUS_FA[k]} for k in order],
    }


def _fillings(rng: np.random.Generator) -> dict:
    rows = []
    for station in STATIONS:
        counts: dict[str, int] = {}
        for _ in range(60):
            roll = float(rng.random())
            leak = False
            # Safe fill is the rated capacity (15 lb). A healthy fill stays under that
            # and inside the 0.28 lb MAV, so only the tails below are anomalies.
            if roll < 0.04:
                leak, actual = True, 14.9
            elif roll < 0.12:
                actual = float(rng.uniform(13.2, 14.4))
            elif roll < 0.17:
                actual = float(rng.uniform(15.05, 15.8))
            else:
                actual = float(np.clip(rng.normal(14.90, 0.05), 14.75, 14.99))
            out = assess_filling(15.0, round(actual, 2), leak, rated_capacity=15.0)
            counts[out["anomaly"]] = counts.get(out["anomaly"], 0) + 1
        n = sum(counts.values())
        short = counts.get("short_fill", 0) / n
        rows.append({
            "station": station,
            "n": n,
            "counts": counts,
            "needs_calibration": short >= 0.12,
            "short_share": round(short, 3),
        })
    total: dict[str, int] = {}
    for row in rows:
        for k, v in row["counts"].items():
            total[k] = total.get(k, 0) + v
    order = ("ok", "short_fill", "overfill", "leak", "tare_loss", "tare_out_of_tolerance")
    return {
        "n": sum(total.values()),
        "target_lb": 15.0,
        "by_anomaly": [{"code": k, "label": ANOMALY_FA[k], "n": total.get(k, 0)} for k in order if total.get(k, 0)],
        "stations": [{
            "station": r["station"],
            "n": r["n"],
            "needs_calibration": r["needs_calibration"],
            "short_share": r["short_share"],
            **{k: r["counts"].get(k, 0) for k in order},
        } for r in rows],
    }


def _distribution(rng: np.random.Generator) -> dict:
    """120 daily points per region, then the real forecaster's 7-day path."""
    bases = {"تهران": 840, "اصفهان": 410, "فارس": 360, "خراسان": 390, "آذربایجان": 300}
    regions = []
    start = AS_OF - timedelta(days=119)
    for name, base in bases.items():
        y = []
        for i in range(120):
            day = start + timedelta(days=i)
            seasonal = 1 + 0.16 * np.sin(2 * np.pi * (day.timetuple().tm_yday / 365.25))
            weekly = 1 + 0.04 * np.sin(2 * np.pi * (day.weekday() / 7))
            noise = float(rng.normal(0, base * 0.03))
            y.append(max(1.0, base * seasonal * weekly + noise))
        y_arr = np.asarray(y, float)
        kind, reason = recommended_kind(len(y_arr), 7)
        model = ForecastModel(kind, 7).fit(y_arr)
        path = model.predict_path(y_arr, 7)
        stock_full = int(base * float(rng.uniform(1.5, 5.0)))
        stock_empty = int(base * float(rng.uniform(0.4, 2.2)))
        need = int(round(sum(path)))
        regions.append({
            "region": name,
            "model": kind,
            "reason": reason,
            "history": [round(float(v), 1) for v in y_arr[-14:]],
            "forecast": [{"date": (AS_OF + timedelta(days=i + 1)).isoformat(), "demand": round(float(p), 1)}
                         for i, p in enumerate(path)],
            "stock_full": stock_full,
            "stock_empty": stock_empty,
            "ship_full": max(0, need - stock_full),
            "collect_empty": max(0, int(round(sum(y_arr[-7:]) * 0.92)) - stock_empty),
        })
    return {"unit": "کپسول در روز", "history_days": 120, "horizon_days": 7, "regions": regions}


def build_operations() -> dict:
    """One seeded draw. Calling it twice returns the same numbers."""
    rng = np.random.default_rng(SEED)
    return {
        "synthetic": True,
        "seed": SEED,
        "as_of": AS_OF.isoformat(),
        "inspection": _inspections(rng),
        "lifecycle": _fleet(rng),
        "filling": _fillings(rng),
        "distribution": _distribution(rng),
    }
