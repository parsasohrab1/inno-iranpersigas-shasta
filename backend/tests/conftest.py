"""Shared fixtures: an isolated temp database, the app client and role tokens.

LCIS_DB_PATH is set BEFORE the app modules are imported, so config.py picks it up.
"""
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
_tmp = tempfile.mkdtemp(prefix="lcis-tests-")
os.environ["LCIS_DB_PATH"] = os.path.join(_tmp, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

USERS = (("admin", "admin123", "admin"), ("inspector1", "insp123", "inspector"),
         ("quality1", "qual123", "quality"), ("producer1", "prod123", "producer"),
         ("distribution1", "dist123", "distribution"), ("auditor1", "audit123", "auditor"))


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_users():
    """Create the login users directly in the DB before any test can log in."""
    from app.core.config import DB_PATH
    from app.core.db import init_db
    from app.core.security import hash_password
    init_db()
    con = sqlite3.connect(DB_PATH)
    for u, p, role in USERS:
        con.execute("INSERT OR REPLACE INTO users (username, password_hash, role, active, created_at) "
                    "VALUES (?,?,?,?,?)", (u, hash_password(p), role, 1, "2025-01-01T00:00:00"))
    con.commit()
    con.close()


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="session")
def tokens(client):
    """Login the seeded demo users once; returns {role: token}."""
    out = {}
    for u, p in (("admin", "admin123"), ("inspector1", "insp123"), ("quality1", "qual123"),
                 ("auditor1", "audit123"), ("distribution1", "dist123")):
        r = client.post("/api/auth/token", data={"username": u, "password": p})
        assert r.status_code == 200, r.text
        out[u] = r.json()["access_token"]
    return out


@pytest.fixture(scope="session")
def admin(client, tokens):
    return {"Authorization": f"Bearer {tokens['admin']}"}


@pytest.fixture(scope="session")
def inspector(client, tokens):
    return {"Authorization": f"Bearer {tokens['inspector1']}"}


@pytest.fixture(scope="session")
def auditor(client, tokens):
    return {"Authorization": f"Bearer {tokens['auditor1']}"}


@pytest.fixture(scope="session")
def seeded(client, admin):
    r = client.post("/api/seed", headers=admin)
    assert r.status_code == 201, r.text
    return r.json()
