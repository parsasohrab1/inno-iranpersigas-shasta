"""FR-09: token endpoint and current-user introspection."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import create_token, current_user, require_role, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

ALLOWED_LOGIN_ROLES = ("admin", "inspector", "quality", "producer", "distribution", "auditor")


@router.post("/token")
def login(form: OAuth2PasswordRequestForm = Depends(), con: Connection = Depends(get_db)):
    row = con.execute("SELECT * FROM users WHERE username = ? AND active = 1", (form.username,)).fetchone()
    if row is None or not verify_password(form.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": create_token(row["username"], row["role"]), "token_type": "bearer",
            "role": row["role"], "expires_in_min": None}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/users", status_code=201)
def create_user(username: str, password: str, role: str,
                user: dict = Depends(require_role("admin")), con: Connection = Depends(get_db)):
    from ..core.security import ROLES, hash_password
    if role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise HTTPException(409, "username already exists")
    con.execute("INSERT INTO users (username, password_hash, role, active, created_at) VALUES (?,?,?,?,?)",
                (username, hash_password(password), role, 1, utcnow()))
    con.commit()
    return {"username": username, "role": role}
