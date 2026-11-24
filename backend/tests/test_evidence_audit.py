"""FR-08 evidence, FR-10 audit trail, seed behaviour, NFR-01 latency."""
import sqlite3
import time

from app.core.config import DB_PATH


def test_evidence_counters_and_validation(client, admin, seeded):
    r = client.get("/api/evidence/rnd", headers=admin)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["counters"]["cylinders"] >= 24 and d["counters"]["fillings"] >= 60
    assert d["validation"]["rules"]["pass"] is True
    assert d["validation"]["forecast"]["selected"] == "ridge"
    assert "forecast_model.json" in d["artefact_sha256"]
    assert d["product_version"].startswith("lcis")


def test_evidence_role_guards(client, inspector, seeded):
    assert client.get("/api/evidence/rnd", headers=inspector).status_code == 403


def test_rnd_activity_crud(client, auditor, seeded):
    r = client.post("/api/evidence/rnd/activities", json={"title": "evaluation of vision thresholds",
                                                          "hours": 3.5, "cost": 0}, headers=auditor)
    assert r.status_code == 201
    lst = client.get("/api/evidence/rnd/activities", headers=auditor).json()
    assert any(a["title"].startswith("evaluation of vision") for a in lst["rows"])
    bad = client.post("/api/evidence/rnd/activities", json={"title": "x", "hours": -2}, headers=auditor)
    assert bad.status_code == 422


def test_audit_trail(client, auditor, seeded):
    r = client.get("/api/audit?limit=10", headers=auditor)
    assert r.status_code == 200 and r.json()["total"] > 0
    rows = r.json()["rows"]
    assert all({"created_at", "actor", "method", "path", "status_code"} <= set(x) for x in rows)
    s = client.get("/api/audit/summary", headers=auditor).json()
    assert s["total"] > 0 and any(a["actor"] == "admin" for a in s["by_actor"])


def test_audit_role_guard(client, inspector, seeded):
    assert client.get("/api/audit", headers=inspector).status_code == 403


def test_seed_guards(client, inspector, admin, seeded):
    assert client.post("/api/seed", headers=inspector).status_code == 403
    assert client.post("/api/seed", headers=admin).status_code == 409
    r = client.post("/api/seed?force=true", headers=admin)
    assert r.status_code == 201 and r.json()["demand_records"] > 200
    st = client.get("/api/seed/status", headers=admin).json()
    assert st["users"] >= 6 and st["demand"] > 200 and st["audit_log"] > 0


def test_seed_demand_is_real(client, admin, seeded):
    """The seeded demand table must carry the real EIA statistics, not synthetic numbers."""
    con = sqlite3.connect(DB_PATH)
    n = con.execute("SELECT COUNT(*) FROM demand WHERE region = 'US_weekly'").fetchone()[0]
    spot = con.execute("SELECT demand FROM demand WHERE region = 'US_weekly' ORDER BY day LIMIT 1").fetchone()[0]
    con.close()
    assert n >= 1100 and 900 < spot < 1600      # weekly US propane/propylene product supplied, kb/d


def test_nfr01_latency_under_500ms(client, admin, seeded):
    """NFR-01: API responses below 500 ms for 1000 records."""
    con = sqlite3.connect(DB_PATH)
    con.executemany(
        "INSERT OR REPLACE INTO cylinders (id, serial, barcode, manufacture_date, last_hydro_test, status, "
        "region, fill_count, fail_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(10_000 + i, f"LC-NFR-{i:05d}", None, "2015-01-01", "2022-01-01", "in_service", "Tehran", 0, 0,
          "2025-01-01T00:00:00") for i in range(1000)])
    con.commit()
    con.close()
    for path in ("/api/cylinders?limit=500", "/api/cylinders?limit=1000",
                 "/api/audit?limit=500", "/api/fillings?limit=200"):
        t0 = time.perf_counter()
        r = client.get(path, headers=admin)
        dt = time.perf_counter() - t0
        assert r.status_code == 200
        assert dt < 0.5, f"{path} took {dt * 1000:.0f} ms"
    total = client.get("/api/cylinders?limit=500&offset=500", headers=admin).json()["total"]
    assert total >= 1000
