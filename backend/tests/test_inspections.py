"""FR-01 cylinders, FR-02 inspections with the SRS verdict rule, FR-03 counters."""
import uuid


def _serial():
    return "LC-TEST-" + uuid.uuid4().hex[:10]


def test_create_and_get_cylinder(client, inspector):
    s = _serial()
    r = client.post("/api/cylinders", json={"serial": s, "manufacture_date": "2015-06-01",
                                            "last_hydro_test": "2022-06-01", "region": "Tehran"},
                    headers=inspector)
    assert r.status_code == 201 and r.json()["status"] == "in_service"
    cid = r.json()["id"]
    got = client.get(f"/api/cylinders/{cid}", headers=inspector).json()
    assert got["serial"] == s and got["fill_count"] == 0
    assert client.get("/api/cylinders/999999", headers=inspector).status_code == 404


def test_duplicate_serial_rejected(client, inspector):
    s = _serial()
    body = {"serial": s, "manufacture_date": "2015-06-01"}
    assert client.post("/api/cylinders", json=body, headers=inspector).status_code == 201
    assert client.post("/api/cylinders", json=body, headers=inspector).status_code == 409


def test_distribution_role_cannot_create(client, tokens, seeded):
    r = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2020-01-01"},
                    headers={"Authorization": f"Bearer {tokens['distribution1']}"})
    assert r.status_code == 403


def test_inspection_verdicts(client, inspector, seeded):
    cid = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2012-03-03"},
                      headers=inspector).json()["id"]
    r_ok = client.post("/api/inspections", json={"cylinder_id": cid, "defects": []}, headers=inspector)
    assert r_ok.status_code == 201 and (r_ok.json()["result"], r_ok.json()["severity"]) == ("pass", "none")
    r_min = client.post("/api/inspections", json={"cylinder_id": cid, "defects": [{"code": "scratch"}]},
                        headers=inspector).json()
    assert (r_min["result"], r_min["severity"]) == ("review", "minor")
    r_crit = client.post("/api/inspections", json={"cylinder_id": cid, "defects": [{"code": "crack"}]},
                         headers=inspector).json()
    assert (r_crit["result"], r_crit["severity"]) == ("reject", "critical")
    cyl = client.get(f"/api/cylinders/{cid}", headers=inspector).json()
    assert cyl["fail_count"] == 1            # FR-03: the rejected inspection counted
    assert cyl["last_inspection_result"] == "reject"


def test_inspection_unknown_defect(client, inspector, seeded):
    cid = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2012-03-03"},
                      headers=inspector).json()["id"]
    r = client.post("/api/inspections", json={"cylinder_id": cid, "defects": [{"code": "nuclear_leak"}]},
                    headers=inspector)
    assert r.status_code == 422 and "known" in r.json()["detail"]


def test_inspection_missing_cylinder(client, inspector, seeded):
    r = client.post("/api/inspections", json={"cylinder_id": 987654, "defects": []}, headers=inspector)
    assert r.status_code == 404


def test_inspection_list(client, auditor, seeded):
    r = client.get("/api/inspections?limit=5", headers=auditor)
    assert r.status_code == 200 and len(r.json()["rows"]) == 5 and r.json()["total"] >= 24
