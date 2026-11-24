"""FR-09 auth and roles."""
import uuid


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_login_wrong_password(client):
    assert client.post("/api/auth/token", data={"username": "admin", "password": "nope"}).status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/token", data={"username": "ghost", "password": "x"})
    assert r.status_code == 401


def test_me_roundtrip(client):
    r = client.post("/api/auth/token", data={"username": "admin", "password": "admin123"})
    tok = r.json()["access_token"]
    assert r.json()["role"] == "admin"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json() == {"username": "admin", "role": "admin"}


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_token(client):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_role_denial(client, inspector, seeded):
    r = client.post("/api/evidence/rnd/activities", json={"title": "x task", "hours": 1}, headers=inspector)
    assert r.status_code == 403


def test_admin_can_create_user(client, admin):
    name = "tmp-" + uuid.uuid4().hex[:8]
    r = client.post(f"/api/auth/users?username={name}&password=pw12345&role=quality", headers=admin)
    assert r.status_code == 201
    r = client.post("/api/auth/token", data={"username": name, "password": "pw12345"})
    assert r.json()["role"] == "quality"


def test_create_user_rejects_bad_role(client, admin):
    assert client.post("/api/auth/users?username=x1&password=p&role=superuser", headers=admin).status_code == 422


def test_duplicate_username(client, admin):
    r = client.post("/api/auth/users?username=admin&password=p&role=quality", headers=admin)
    assert r.status_code == 409
