"""FR-09 authentication and roles: pbkdf2 password hashes, HS256 bearer tokens, role dependencies.

Roles follow the SRS actors: admin (مدیر سیستم), inspector (اپراتور بازرسی), quality (کارشناس کیفیت),
producer (مدیر تولید), distribution (مدیر توزیع), auditor (حسابرس/کارشناس مالیاتی R&D).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .config import ACCESS_TOKEN_MIN_ROUNDS, SECRET, TOKEN_ALGORITHM, TOKEN_TTL_MIN

ROLES = ("admin", "inspector", "quality", "producer", "distribution", "auditor")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


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


def create_token(username: str, role: str, ttl_min: int = TOKEN_TTL_MIN) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=ttl_min)},
                      SECRET, algorithm=TOKEN_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET, algorithms=[TOKEN_ALGORITHM])
    except jwt.PyJWTError as e:  # noqa: F841 - message goes into the HTTP error below
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def current_user(token: str | None = Depends(oauth2)) -> dict:
    """Dependency: any authenticated user. Returns {"username", "role"}."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    payload = decode_token(token)
    return {"username": payload["sub"], "role": payload["role"]}


def require_role(*roles: str):
    """Dependency factory: only the listed roles may call the endpoint."""
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"role '{user['role']}' is not allowed here (needs one of {sorted(roles)})")
        return user
    return dep
