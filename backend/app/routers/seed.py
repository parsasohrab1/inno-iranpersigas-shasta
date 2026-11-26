"""POST /api/seed: deterministic demo data. The demand records come from the REAL EIA series."""
from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection

from ..core.config import DEFAULT_ADMIN, EIA_DIR, ENV
from ..core.db import get_db, utcnow
from ..core.security import hash_password, require_role
from ..services.lifecycle import assess_inspection

router = APIRouter(prefix="/api/seed", tags=["seed"])

DEMO_USERS = [("inspector1", "insp123", "inspector"), ("quality1", "qual123", "quality"),
              ("producer1", "prod123", "producer"), ("distribution1", "dist123", "distribution"),
              ("auditor1", "audit123", "auditor")]
REGIONS_FA = ["Tehran", "Isfahan", "Fars", "Khorasan", "Azarbaijan"]
STATIONS = ["ST-001", "ST-002", "ST-003", "ST-004"]
RND_SEED = [
    ("Web image dataset collection and two-stage VLM verification (563 candidates)", 42.0),
    ("Rule engines (filling, lot acceptance, lifecycle) + boundary tests + NIST replay", 58.0),
    ("Forecasting engine: 7 methods, walk-forward back-test, seasonal selection policy", 51.0),
    ("Vision model: features, training, verdict thresholds on the verified dataset", 36.0),
    ("FastAPI platform: auth/roles, audit, evidence, tests, Docker, CI", 44.0),
]


def _seed_demand(con: Connection) -> int:
    n = 0
    for fname, region in ((os.path.join(EIA_DIR, "eia_weekly_us_propane_propylene_product_supplied.csv"),
                           "US_weekly"),
                          (os.path.join(EIA_DIR, "eia_monthly_padd_product_supplied.csv"), None)):
        df = pd.read_csv(fname)
        if region is None:
            df["region"] = df["region"].astype(str)
        else:
            df["region"] = region
        for r in df.itertuples(index=False):
            con.execute("INSERT OR REPLACE INTO demand (region, day, demand) VALUES (?,?,?)",
                        (r.region, str(r.date)[:10], float(r.value)))
            n += 1
    return n


@router.post("", status_code=201)
def seed(force: bool = False, user: dict = Depends(require_role("admin")), con: Connection = Depends(get_db)):
    if ENV == "production":
        raise HTTPException(403, "seed is disabled when LCIS_ENV=production")
    have = con.execute("SELECT COUNT(*) FROM cylinders").fetchone()[0]
    if have and not force:
        raise HTTPException(409, "database already seeded; call with force=true to reseed (wipes the tables)")
    if force:
        for t in ("inspections", "fillings", "cylinder_events", "vision_verdicts", "cylinders", "users",
                  "demand", "rnd_activities"):
            con.execute(f"DELETE FROM {t}")  # noqa: S608 - table names are literals above; children first
    rng = random.Random(0)                        # deterministic seed data
    now = utcnow()
    users = [(DEFAULT_ADMIN[0], DEFAULT_ADMIN[1], "admin")] + DEMO_USERS
    for u, p, role in users:
        con.execute("INSERT OR REPLACE INTO users (username, password_hash, role, active, created_at) "
                    "VALUES (?,?,?,?,?)", (u, hash_password(p), role, 1, now))
    for k in range(1, 25):                        # FR-01
        made = date(2008 + (k % 15), (k % 12) + 1, ((k * 7) % 27) + 1)
        tested = date(2021 + (k % 3), ((k * 5) % 12) + 1, ((k * 11) % 27) + 1)
        con.execute("INSERT INTO cylinders (serial, barcode, manufacture_date, last_hydro_test, status, "
                    "region, fill_count, fail_count, created_at) VALUES (?,?,?,?,?,?,0,0,?)",
                    (f"LC-2024-{k:04d}", f"626{k:06d}", made.isoformat(), tested.isoformat(),
                     "in_service", REGIONS_FA[k % 5], now))
    cyl_ids = [r[0] for r in con.execute("SELECT id FROM cylinders ORDER BY id").fetchall()]
    defect_sets = [[], ["rust"], ["scratch"], ["dent"], ["crack"], ["valve_damage"], ["label_missing"], []]
    for i, cid in enumerate(cyl_ids):             # FR-02
        defects = defect_sets[i % len(defect_sets)]
        res = assess_inspection(defects)
        when = f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T09:{i % 60:02d}:00"
        con.execute("INSERT INTO inspections (cylinder_id, created_at, defects_json, result, severity, actor) "
                    "VALUES (?,?,?,?,?,?)",
                    (cid, when, json.dumps([{"code": d} for d in defects]),
                     res, {"reject": "critical", "review": "minor", "pass": "none"}[res], "inspector1"))
        if res == "reject":
            con.execute("UPDATE cylinders SET fail_count = fail_count + 1, status = 'rejected' WHERE id = ?",
                        (cid,))
    for i in range(60):                           # FR-05
        cid = cyl_ids[i % len(cyl_ids)]
        leak = i % 17 == 0
        actual = 15.0 + rng.uniform(-0.6, 0.5) if not leak else 14.9
        if i % 9 == 0:
            actual = 13.6                          # short fill
        if i % 21 == 0:
            actual = 15.9                          # overfill above the safe fill of 15 lb
        out = {"anomaly": "ok"}
        try:
            from ..services.filling import assess_filling
            out = assess_filling(15.0, round(actual, 2), leak, rated_capacity=15.0)
        except ValueError:
            pass
        con.execute("INSERT INTO fillings (cylinder_id, station_id, created_at, target_weight, actual_weight, "
                    "leak_signal, anomaly, action, detail, actor) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid, STATIONS[i % 4], f"2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T11:00:00",
                     15.0, round(actual, 2), int(leak), out.get("anomaly", "ok"), out.get("action", "accept"),
                     out.get("detail", ""), "inspector1"))
        con.execute("UPDATE cylinders SET fill_count = fill_count + 1 WHERE id = ?", (cid,))
        if out.get("action") == "reject":
            con.execute("UPDATE cylinders SET fail_count = fail_count + 1, status = 'rejected' WHERE id = ?",
                        (cid,))
    n_demand = _seed_demand(con)
    for title, hours in RND_SEED:                 # FR-08 (costs are entered by the finance team)
        con.execute("INSERT INTO rnd_activities (created_at, title, hours, cost, note, actor) "
                    "VALUES (?,?,?,?,?,?)", (now, title, hours, 0.0, "hours are documented estimates",
                                             "admin"))
    con.commit()
    return {"seeded": True, "users": len(users), "cylinders": len(cyl_ids), "inspections": len(cyl_ids),
            "fillings": 60, "demand_records": n_demand, "rnd_activities": len(RND_SEED)}


@router.get("/status")
def seed_status(user: dict = Depends(require_role("admin")), con: Connection = Depends(get_db)):
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - literal table names
            for t in ("users", "cylinders", "inspections", "fillings", "demand", "rnd_activities",
                      "audit_log")}
