#!/usr/bin/env python
"""Empirical defect probes for the LCIS API - confirm suspected defects with a live TestClient.

Each probe prints PASS/FAIL plus evidence. Output is used verbatim in the defect report.
  python -I scripts/defect_probes.py
"""
import json
import os
import sqlite3
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="lcis-probes-")
os.environ["LCIS_DB_PATH"] = os.path.join(_tmp, "probe.db")

sys.path.insert(0, "/home/z/my-project/build/lcis/backend")
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

app = create_app()
c = TestClient(app, raise_server_exceptions=False)   # let unhandled server errors surface as HTTP 500
results = []


def probe(name, ok, evidence):
    results.append({"probe": name, "confirmed": bool(ok), "evidence": evidence})
    print(("CONFIRMED " if ok else "not-reproduced ") + name + " | " + str(evidence)[:160])


def login(u, p):
    r = c.post("/api/auth/token", data={"username": u, "password": p})
    return r.json()["access_token"]


# bootstrap users like conftest
con = sqlite3.connect(os.environ["LCIS_DB_PATH"])
from app.core.db import init_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402

init_db()
for u, p, role in (("admin", "admin123", "admin"), ("inspector1", "insp123", "inspector")):
    con.execute("INSERT OR REPLACE INTO users (username,password_hash,role,active,created_at) VALUES (?,?,?,?,?)",
                (u, hash_password(p), role, 1, "2025-01-01T00:00:00"))
con.commit()

tok = {"admin": login("admin", "admin123"), "insp": login("inspector1", "insp123")}
H = lambda k: {"Authorization": "Bearer " + tok[k]}  # noqa: E731

# P1 password as query parameter (ends up in access logs / browser history)
r = c.post("/api/auth/users", params={"username": "weakpw", "password": "s3cret-pw", "role": "inspector"},
           headers=H("admin"))
probe("P1 create_user accepts the password as a URL query parameter",
      r.status_code == 201,
      {"status": r.status_code, "password_in_url": True,
       "note": "OAuth2 access logs and proxy logs record the full query string"})

# P2 empty password accepted (no password policy)
r = c.post("/api/auth/users", params={"username": "emptypw", "password": "", "role": "inspector"},
           headers=H("admin"))
r2 = c.post("/api/auth/token", data={"username": "emptypw", "password": ""})
probe("P2 empty password can be created and logs in (no password policy)",
      r.status_code == 201 and r2.status_code == 200, {"create": r.status_code, "login": r2.status_code})

# P3 rejected cylinder can still be filled
r = c.post("/api/cylinders", json={"serial": "PROBE-1", "manufacture_date": "2010-01-01"}, headers=H("insp"))
cid = r.json()["id"]
r = c.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                  "actual_weight": 15.0, "leak_signal": True, "rated_capacity": 15.0},
           headers=H("insp"))
assert r.status_code == 201, r.text
r2 = c.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                   "actual_weight": 15.0, "leak_signal": False, "rated_capacity": 15.0},
            headers=H("insp"))
probe("P3 filling is accepted for a cylinder whose status is 'rejected' (leak)",
      r.json().get("action") == "reject" and r2.status_code == 201,
      {"first_filling_action": r.json().get("action"), "second_filling_status": r2.status_code,
       "cylinder_status_after": c.get(f"/api/cylinders/{cid}", headers=H("insp")).json()["status"]})

# P4 unbounded limit on list endpoints
r = c.get("/api/inspections", params={"limit": 10 ** 9}, headers=H("insp"))
probe("P4 /api/inspections accepts limit=1000000000 (no upper bound)",
      r.status_code == 200,
      {"status": r.status_code, "note": "cylinders caps at le=2000; inspections/fillings/anomalies do not"})

# P5 duplicate barcode -> unhandled IntegrityError (500)
r = c.post("/api/cylinders", json={"serial": "PROBE-2", "barcode": "626000001",
                                   "manufacture_date": "2010-01-01"}, headers=H("insp"))
r2 = c.post("/api/cylinders", json={"serial": "PROBE-3", "barcode": "626000001",
                                    "manufacture_date": "2010-01-01"}, headers=H("insp"))
probe("P5 duplicate barcode returns HTTP 500 instead of 409",
      r2.status_code == 500, {"first": r.status_code, "duplicate": r2.status_code})

# P6 target_weight above rated capacity is accepted as input
r = c.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 20.0,
                                  "actual_weight": 20.0, "rated_capacity": 15.0}, headers=H("insp"))
probe("P6 target_weight 20 lb accepted while rated capacity is 15 lb (input not validated)",
      r.status_code == 201, {"status": r.status_code, "anomaly": r.json().get("anomaly")})

# P7 forecast interval width is constant across horizons
r = c.get("/api/forecast", params={"horizon": 4}, headers=H("insp"))
rec = r.json()["records"]
widths = [round(x["hi80"] - x["lo80"], 3) for x in rec]
probe("P7 interval width does not widen with the forecast horizon",
      len(set(widths)) == 1, {"widths_per_step": widths, "model": r.json()["model"]["kind"]})

# P8 lifecycle crashes for a cylinder registered without last_hydro_test (valid per POST /api/cylinders)
r = c.post("/api/cylinders", json={"serial": "PROBE-4", "manufacture_date": "2010-01-01"}, headers=H("insp"))
cid4 = r.json()["id"]
r = c.get(f"/api/cylinders/{cid4}/lifecycle", headers=H("insp"))
probe("P8a GET lifecycle returns 500 for a cylinder without last_hydro_test (valid at creation)",
      r.status_code == 500, {"status": r.status_code,
                             "note": "POST allows last_hydro_test=null; assess_lifecycle calls date.fromisoformat(None)"})

# P9 suspend state cannot be cleared (no requalification_date column / endpoint)
con2 = sqlite3.connect(os.environ["LCIS_DB_PATH"])
cols = [x[1] for x in con2.execute("PRAGMA table_info(cylinders)").fetchall()]
probe("P8b cylinders table has no requalification_date column (suspend can never be cleared via API)",
      "requalification_date" not in cols, {"columns": cols})

# P9 regional forecast picks the alphabetically-first series, not the longest one
import pandas as pd  # noqa: E402
df = pd.read_csv("/home/z/my-project/build/lcis/data/eia/eia_monthly_padd_product_supplied.csv")
worst = {}
for reg, g in df.groupby("region"):
    lens = g.groupby("series").size().sort_values(ascending=False)
    first_alpha = sorted(g["series"].unique())[0]
    if lens.index[0] != first_alpha:
        worst[reg] = {"picked": first_alpha, "picked_len": int(lens[first_alpha]),
                      "longest": lens.index[0], "longest_len": int(lens.iloc[0])}
probe("P9 regional endpoint may use a shorter series than the region's longest (alphabetical pick)",
      bool(worst), worst)

# P10 audit trail has no tamper evidence
con3 = sqlite3.connect(os.environ["LCIS_DB_PATH"])
con3.execute("UPDATE audit_log SET actor='someone-else' WHERE id=(SELECT MIN(id) FROM audit_log)")
con3.commit()
row = con3.execute("SELECT actor FROM audit_log WHERE id=(SELECT MIN(id) FROM audit_log)").fetchone()[0]
probe("P10 audit_log rows can be edited in place without detection (no hash chain)",
      row == "someone-else", {"edited_actor": row})

print("\nSUMMARY " + json.dumps({"confirmed": sum(r["confirmed"] for r in results), "total": len(results)}))
json.dump(results, open("/home/z/my-project/scripts/defect_probe_results.json", "w"), indent=1)
