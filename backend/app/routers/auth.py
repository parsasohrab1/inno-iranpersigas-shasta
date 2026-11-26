"""FR-09: token endpoint, logout and user administration.

D-04: the password is a JSON body field, never a query parameter.
D-07: failed logins are throttled per client address and username.
D-08: new passwords must meet the length and common-password policy.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import (ROLES, bump_token_version, create_token, current_user, password_problem,
                             require_role, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_FAILS: dict[tuple[str, str], list[float]] = {}
_LOCK_AFTER = 5
_WINDOW_S = 60.0


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)
    role: str


def _client_key(request: Request, username: str) -> tuple[str, str]:
    host = request.client.host if request.client else "unknown"
    return host, (username or "").lower()


def _locked(key: tuple[str, str]) -> bool:
    now = time.monotonic()
    recent = [t for t in _FAILS.get(key, []) if now - t < _WINDOW_S]
    if recent:
        _FAILS[key] = recent
    elif key in _FAILS:
        del _FAILS[key]
    return len(recent) >= _LOCK_AFTER


def _note_failure(key: tuple[str, str]) -> None:
    now = time.monotonic()
    recent = [t for t in _FAILS.get(key, []) if now - t < _WINDOW_S]
    recent.append(now)
    _FAILS[key] = recent


def _note_success(key: tuple[str, str]) -> None:
    _FAILS.pop(key, None)


@router.post("/token")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), con: Connection = Depends(get_db)):
    key = _client_key(request, form.username)
    if _locked(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "too many failed logins; try again in a minute")
    row = con.execute("SELECT * FROM users WHERE username = ? AND active = 1", (form.username,)).fetchone()
    if row is None or not verify_password(form.password, row["password_hash"]):
        _note_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    _note_success(key)
    return {"access_token": create_token(row["username"], row["role"], version=int(row["token_version"] or 0)),
            "token_type": "bearer", "role": row["role"], "expires_in_min": None}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@router.post("/logout")
def logout(user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    """D-09: bump the account's token version so the presented bearer token stops working."""
    bump_token_version(con, user["username"])
    con.commit()
    return {"logged_out": True, "username": user["username"]}


@router.post("/users", status_code=201)
def create_user(body: UserIn, user: dict = Depends(require_role("admin")), con: Connection = Depends(get_db)):
    from ..core.security import hash_password
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)
    if con.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone():
        raise HTTPException(409, "username already exists")
    con.execute("INSERT INTO users (username, password_hash, role, active, created_at) VALUES (?,?,?,?,?)",
                (body.username, hash_password(body.password), body.role, 1, utcnow()))
    con.commit()
    return {"username": body.username, "role": body.role}
