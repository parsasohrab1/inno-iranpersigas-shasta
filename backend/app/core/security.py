"""FR-09 authentication and roles: pbkdf2 password hashes, HS256 bearer tokens, role dependencies.

Roles follow the SRS actors: admin (مدیر سیستم), inspector (اپراتور بازرسی), quality (کارشناس کیفیت),
producer (مدیر تولید), distribution (مدیر توزیع), auditor (حسابرس/کارشناس مالیاتی R&D).

D-09: every request re-reads the user row. A deactivated account or a bumped token_version (logout,
password change) rejects the bearer token immediately instead of waiting for the 8-hour expiry.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlite3 import Connection

from .config import ACCESS_TOKEN_MIN_ROUNDS, MIN_PASSWORD_LEN, SECRET, TOKEN_ALGORITHM, TOKEN_TTL_MIN
from .db import get_db

ROLES = ("admin", "inspector", "quality", "producer", "distribution", "auditor")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

# D-08: short or widely published passwords are refused at account creation.
COMMON_PASSWORDS = {
    "password", "password1", "password123", "password1234", "admin123", "admin1234",
    "changeme", "changeme123", "1234567890", "123456789", "qwertyuiop", "letmein123",
    "insp123456", "iloveyou1", "welcome123",
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ACCESS_TOKEN_MIN_ROUNDS)
    return f"pbkdf2${ACCESS_TOKEN_MIN_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, dk = stored.split("$")
        cand = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(cand.hex(), dk)
    except (ValueError, AttributeError):
        return False


def password_problem(password: str) -> str | None:
    """None when the password meets the policy, otherwise a short reason."""
    if password is None or len(password) < MIN_PASSWORD_LEN:
        return f"password must be at least {MIN_PASSWORD_LEN} characters"
    if password.lower().strip() in COMMON_PASSWORDS:
        return "password is too common"
    return None


def create_token(username: str, role: str, ttl_min: int = TOKEN_TTL_MIN, version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": username, "role": role, "ver": int(version), "iat": now,
         "exp": now + timedelta(minutes=ttl_min)},
        SECRET, algorithm=TOKEN_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET, algorithms=[TOKEN_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def current_user(token: str | None = Depends(oauth2), con: Connection = Depends(get_db)) -> dict:
    """Dependency: any authenticated, still-active user. Returns {"username", "role"} from the database."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    payload = decode_token(token)
    row = con.execute(
        "SELECT username, role, active, token_version FROM users WHERE username = ?",
        (payload.get("sub"),)).fetchone()
    if row is None or not row["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    if int(payload.get("ver", 0)) != int(row["token_version"] or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})
    return {"username": row["username"], "role": row["role"]}


def require_role(*roles: str):
    """Dependency factory: only the listed roles may call the endpoint."""
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"role '{user['role']}' is not allowed here (needs one of {sorted(roles)})")
        return user
    return dep


def bump_token_version(con: Connection, username: str) -> None:
    """Invalidate every bearer token already issued to this account (logout / password change)."""
    con.execute("UPDATE users SET token_version = token_version + 1 WHERE username = ?", (username,))
