"""FR-04 lifecycle assessment, FR-05 filling control, FR-06 anomalies."""
import uuid


def _new_cyl(client, headers, made="2010-01-01", tested="2021-01-01"):
    return client.post("/api/cylinders", json={"serial": "LC-LC-" + uuid.uuid4().hex[:8],
                                               "manufacture_date": made, "last_hydro_test": tested},
                       headers=headers).json()["id"]


def test_lifecycle_fields(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    r = client.get(f"/api/cylinders/{cid}/lifecycle", headers=inspector).json()
    for k in ("status", "test_state", "next_test_date", "priority_index", "priority_level", "recommendation",
              "rule_sources"):
        assert k in r
    assert r["test_state"] == "overdue"           # tested 2021 -> overdue by now (2026)
    assert r["days_to_test"] < 0


def test_lifecycle_suspends_after_reject(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    client.post("/api/inspections", json={"cylinder_id": cid, "defects": [{"code": "valve_damage"}]},
                headers=inspector)
    r = client.get(f"/api/cylinders/{cid}/lifecycle", headers=inspector).json()
    assert r["status"] == "suspend" and r["priority_level"] == "high"
    assert any("requalification" in x for x in r["reasons"])


def test_filling_ok(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    r = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                           "actual_weight": 15.05}, headers=inspector).json()
    assert (r["anomaly"], r["action"]) == ("ok", "accept")
    cyl = client.get(f"/api/cylinders/{cid}", headers=inspector).json()
    assert cyl["fill_count"] == 1                 # FR-03


def test_filling_leak_rejects(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    r = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                           "actual_weight": 15.0, "leak_signal": True}, headers=inspector).json()
    assert (r["anomaly"], r["action"], r["severity"]) == ("leak", "reject", "critical")
    cyl = client.get(f"/api/cylinders/{cid}", headers=inspector).json()
    assert cyl["status"] == "rejected" and cyl["fail_count"] == 1


def test_filling_short_fill(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    r = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                           "actual_weight": 13.0}, headers=inspector).json()
    assert r["anomaly"] == "short_fill" and r["action"] == "rework"
    assert "MAV" in r["detail"]


def test_filling_overfill(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    r = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                           "actual_weight": 15.5, "rated_capacity": 15.0},
                    headers=inspector).json()
    assert r["anomaly"] == "overfill" and r["action"] == "reject"
    assert r["overfill_severity"] == "up_to_5_pct"    # 0.5 lb over a 15 lb capacity = 3.3 %
    r2 = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                            "actual_weight": 15.9, "rated_capacity": 15.0},
                     headers=inspector).json()
    assert r2["overfill_severity"] == "5_to_10_pct"   # 6 % over lands in the next NIST band


def test_filling_validation_errors(client, inspector, seeded):
    cid = _new_cyl(client, inspector)
    assert client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": -1,
                                              "actual_weight": 1}, headers=inspector).status_code == 422
    assert client.post("/api/fillings", json={"cylinder_id": 424242, "station_id": "ST-9",
                                              "target_weight": 15, "actual_weight": 15},
                       headers=inspector).status_code == 404


def test_anomalies_endpoint(client, inspector, auditor, seeded):
    assert client.get("/api/fillings/anomalies", headers=inspector).status_code == 200
    data = client.get("/api/fillings/anomalies", headers=auditor).json()
    assert data["total"] >= 5 and all(r["anomaly"] != "ok" for r in data["rows"])
    assert all("serial" in r for r in data["rows"])
