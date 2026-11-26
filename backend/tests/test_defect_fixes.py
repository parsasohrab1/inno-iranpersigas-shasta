"""Boundary tests for the defects confirmed by the 2026-11-26 audit."""
import io
import json
import sqlite3
import uuid
from datetime import date

import numpy as np
import pytest
from PIL import Image

from app.core.config import DB_PATH
from app.routers import vision_api
from app.services.vision import CLASSES, DEFAULT_THRESHOLDS, VisionModel, features_from_bytes


@pytest.fixture()
def fake_vision(monkeypatch, tmp_path):
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    dim = 8
    centers = np.stack([rng.normal(3.0 * i, 0.2, dim) for i in range(len(CLASSES))])
    X = np.vstack([rng.normal(c, 0.3, (12, dim)) for c in centers])
    y = sum([[c] * 12 for c in CLASSES], [])
    clf = LogisticRegression(max_iter=1000).fit(X, y)
    model = VisionModel(classes=list(CLASSES), scaler_mean=X.mean(axis=0), scaler_scale=X.std(axis=0) + 1e-9,
                        coef=clf.coef_, intercept=clf.intercept_, thresholds=dict(DEFAULT_THRESHOLDS),
                        meta={"feature_version": "test", "n_train": len(y), "test_accuracy": 1.0,
                              "limitations": "synthetic"})
    json.dump(model.to_dict(), open(tmp_path / "vision_model.json", "w"))
    monkeypatch.setattr(vision_api, "ML_DIR", str(tmp_path))
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)
    # The stored artefact is 8-D, but the endpoint extracts real image features. Swap classify's
    # length check by pointing the loaded model at a vector of the real feature length is unnecessary:
    # this fixture is only used to exercise storage after we monkeypatch features_from_bytes.
    real = vision_api.vision.features_from_bytes
    monkeypatch.setattr(vision_api.vision, "features_from_bytes", lambda data: np.zeros(dim))
    yield model
    monkeypatch.setattr(vision_api.vision, "features_from_bytes", real)
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)


def _serial():
    return "LC-FIX-" + uuid.uuid4().hex[:10]


def test_password_is_not_accepted_on_the_query_string(client, admin):
    r = client.post("/api/auth/users?username=fromquery&password=Temp-pass-123&role=inspector", headers=admin)
    assert r.status_code == 422


def test_short_and_common_passwords_rejected(client, admin):
    short = client.post("/api/auth/users", json={"username": "shortpw1", "password": "short",
                                                 "role": "inspector"}, headers=admin)
    common = client.post("/api/auth/users", json={"username": "commonpw1", "password": "password1234",
                                                  "role": "inspector"}, headers=admin)
    assert short.status_code == 422 and common.status_code == 422


def test_login_lockout_after_repeated_failures(client):
    body = {"username": "nosuch-lockout", "password": "wrong-password"}
    for _ in range(5):
        assert client.post("/api/auth/token", data=body).status_code == 401
    assert client.post("/api/auth/token", data=body).status_code == 429


def test_logout_revokes_the_token(client, admin):
    name = "bye-" + uuid.uuid4().hex[:8]
    assert client.post("/api/auth/users", json={"username": name, "password": "Temp-pass-123", "role": "auditor"},
                       headers=admin).status_code == 201
    tok = client.post("/api/auth/token", data={"username": name, "password": "Temp-pass-123"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    tok2 = client.post("/api/auth/token", data={"username": name, "password": "Temp-pass-123"}).json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok2}"}).status_code == 200


def test_deactivated_user_token_stops_working(client, admin):
    name = "off-" + uuid.uuid4().hex[:8]
    assert client.post("/api/auth/users", json={"username": name, "password": "Temp-pass-123", "role": "auditor"},
                       headers=admin).status_code == 201
    tok = client.post("/api/auth/token", data={"username": name, "password": "Temp-pass-123"}).json()["access_token"]
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE users SET active = 0 WHERE username = ?", (name,))
    con.commit()
    con.close()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_lifecycle_without_hydro_test(client, inspector):
    r = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2010-01-01"},
                    headers=inspector)
    assert r.status_code == 201, r.text
    life = client.get(f"/api/cylinders/{r.json()['id']}/lifecycle", headers=inspector)
    assert life.status_code == 200, life.text
    body = life.json()
    assert body["test_state"] == "overdue"
    assert any("no periodic test" in x for x in body["reasons"])


def test_duplicate_barcode_is_409(client, inspector):
    barcode = "626" + uuid.uuid4().hex[:8]
    body = {"serial": _serial(), "barcode": barcode, "manufacture_date": "2015-01-01"}
    assert client.post("/api/cylinders", json=body, headers=inspector).status_code == 201
    body["serial"] = _serial()
    assert client.post("/api/cylinders", json=body, headers=inspector).status_code == 409


def test_rejected_cylinder_returns_to_service_only_after_requalification(client, inspector, admin):
    cid = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2012-01-01",
                                              "last_hydro_test": "2024-01-01"},
                      headers=inspector).json()["id"]
    leak = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                              "actual_weight": 15.0, "leak_signal": True}, headers=inspector)
    assert leak.status_code == 201 and leak.json()["action"] == "reject"
    again = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                               "actual_weight": 15.0}, headers=inspector)
    assert again.status_code == 409
    back = client.post(f"/api/cylinders/{cid}/requalification",
                       json={"requalification_date": date.today().isoformat(), "passed": True, "note": "retested"},
                       headers=admin)
    assert back.status_code == 201 and back.json()["returned_to_service"] is True
    ok = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 15.0,
                                            "actual_weight": 15.0}, headers=inspector)
    assert ok.status_code == 201 and ok.json()["anomaly"] == "ok"


def test_target_above_rated_capacity_is_422(client, inspector):
    cid = client.post("/api/cylinders", json={"serial": _serial(), "manufacture_date": "2018-01-01",
                                              "last_hydro_test": "2024-06-01"}, headers=inspector).json()["id"]
    r = client.post("/api/fillings", json={"cylinder_id": cid, "station_id": "ST-9", "target_weight": 20.0,
                                           "actual_weight": 15.0, "rated_capacity": 15.0}, headers=inspector)
    assert r.status_code == 422


def test_list_limit_is_capped(client, inspector):
    assert client.get("/api/inspections", params={"limit": 10 ** 9}, headers=inspector).status_code == 422
    assert client.get("/api/fillings", params={"limit": 10 ** 9}, headers=inspector).status_code == 422
    assert client.get("/api/fillings/anomalies", params={"limit": 10 ** 9}, headers=inspector).status_code == 422


def test_fleet_summary(client, inspector, seeded):
    r = client.get("/api/cylinders/fleet/summary", headers=inspector)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] >= 24 and "by_status" in body and "by_priority" in body


def test_rnd_total_is_the_table_count_not_the_page(client, auditor, seeded):
    listed = client.get("/api/evidence/rnd/activities?limit=1", headers=auditor).json()
    assert listed["total"] >= 5 and len(listed["rows"]) == 1


def test_audit_chain_detects_tampering(client, auditor):
    assert client.get("/api/audit/verify", headers=auditor).json()["valid"] is True
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT id, actor FROM audit_log ORDER BY id LIMIT 1").fetchone()
    con.execute("UPDATE audit_log SET actor = ? WHERE id = ?", ("tampered-actor", row[0]))
    con.commit()
    broken = client.get("/api/audit/verify", headers=auditor).json()
    con.execute("UPDATE audit_log SET actor = ? WHERE id = ?", (row[1], row[0]))
    con.commit()
    con.close()
    assert broken["valid"] is False and broken["broken_at"] == row[0]
    assert client.get("/api/audit/verify", headers=auditor).json()["valid"] is True


def test_seed_is_refused_in_production(client, admin, monkeypatch):
    monkeypatch.setattr("app.routers.seed.ENV", "production")
    assert client.post("/api/seed?force=true", headers=admin).status_code == 403


def test_dashboard_is_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "داشبورد" in r.text


def test_vision_stores_a_verdict_and_rejects_a_short_feature_vector(client, inspector, fake_vision):
    rng = np.random.default_rng(2)
    arr = rng.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    r = client.post("/api/vision/inspect", files={"file": ("c.png", buf.getvalue(), "image/png")},
                    headers=inspector)
    assert r.status_code == 200, r.text
    assert r.json()["id"] >= 1 and len(r.json()["image"]["sha256"]) == 64
    with pytest.raises(ValueError):
        fake_vision.classify(np.zeros(3))


def test_vision_byte_cap(client, inspector, fake_vision, monkeypatch):
    monkeypatch.setattr("app.routers.vision_api.MAX_IMAGE_BYTES", 30)
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    r = client.post("/api/vision/inspect", files={"file": ("c.png", buf.getvalue(), "image/png")},
                    headers=inspector)
    assert r.status_code == 413


def test_pixel_limit(monkeypatch):
    previous = Image.MAX_IMAGE_PIXELS
    monkeypatch.setattr("app.services.vision.MAX_IMAGE_PIXELS", 10)
    rng = np.random.default_rng(4)
    arr = rng.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    try:
        with pytest.raises(ValueError, match="pixel limit"):
            features_from_bytes(buf.getvalue())
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def test_mismatched_artefact_is_refused():
    with pytest.raises(ValueError, match="dimension"):
        VisionModel.from_dict({
            "classes": ["ok"], "thresholds": {}, "scaler_mean": [0.0, 1.0], "scaler_scale": [1.0, 1.0],
            "coef": [[0.2]], "intercept": [0.0], "meta": {},
        })
