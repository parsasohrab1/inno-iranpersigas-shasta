"""FR-07 forecast endpoint on the REAL EIA series."""
import math


def test_national_weekly(client, inspector):
    r = client.get("/api/forecast?horizon=7", headers=inspector)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["period"] == 52 and len(d["records"]) == 7
    assert d["model"]["kind"] == "ridge"          # the validated policy: >= 11 seasons -> ridge
    assert "daily" in d["note"]                   # honest scope note is always returned
    for a, b in zip(d["records"], d["records"][1:]):
        assert a["date"] < b["date"] and a["lo80"] <= a["hi80"]


def test_regional_monthly(client, inspector):
    r = client.get("/api/forecast?region=Gulf&horizon=4", headers=inspector)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["period"] == 12 and "PADD 3" in d["series"] and len(d["records"]) == 4
    assert d["unit"].startswith("thousand barrels")


def test_unknown_region_404(client, inspector):
    assert client.get("/api/forecast?region=Atlantis", headers=inspector).status_code == 404


def test_horizon_and_auth_guards(client):
    assert client.get("/api/forecast?horizon=0", headers=None).status_code == 401
    assert client.get("/api/forecast?horizon=0",
                      headers={"Authorization": "Bearer x"}).status_code == 401
    r = client.post("/api/auth/token", data={"username": "inspector1", "password": "insp123"})
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/forecast?horizon=0", headers=tok).status_code == 422
    assert client.get("/api/forecast?horizon=999", headers=tok).status_code == 422


def test_forecast_values_are_finite(client, inspector):
    d = client.get("/api/forecast?horizon=3", headers=inspector).json()
    for rec in d["records"]:
        assert all(math.isfinite(rec[k]) and rec[k] >= 0 for k in ("demand", "lo80", "hi80"))
