"""FR-10 audit trail: every API call is recorded with actor, endpoint, status and duration (NFR-01 probe too)."""
from __future__ import annotations

import json
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .db import connect, utcnow


class AuditMiddleware(BaseHTTPMiddleware):
    """Write one audit_log row per request. Failures of logging must never break the API."""

    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        actor = None
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            try:  # decode without validating expiry strictly - audit is a trail, not a gate
                import jwt
                from .config import SECRET
                payload = jwt.decode(auth[7:], SECRET, algorithms=["HS256"], options={"verify_exp": False})
                actor = payload.get("sub")
            except Exception:  # noqa: BLE001 - invalid tokens are logged as anonymous
                actor = None
        try:
            response = await call_next(request)
            status_code, detail = response.status_code, ""
        except Exception as e:  # noqa: BLE001
            status_code, detail = 500, f"{type(e).__name__}: {e}"
            raise
        finally:
            try:
                con = connect()
                con.execute(
                    "INSERT INTO audit_log (created_at, actor, method, path, status_code, duration_ms, detail) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (utcnow(), actor, request.method, request.url.path, status_code,
                     round((time.perf_counter() - t0) * 1000, 2), detail or None))
                con.commit()
                con.close()
            except Exception:  # noqa: BLE001 - the audit DB may not exist yet during tests
                pass
        return response


def audit_query(con, limit: int = 100, offset: int = 0, actor: str | None = None):
    q = "SELECT * FROM audit_log"
    args: list = []
    if actor:
        q += " WHERE actor = ?"
        args.append(actor)
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    rows = [dict(r) for r in con.execute(q, args).fetchall()]
    n = con.execute("SELECT COUNT(*) FROM audit_log" + (" WHERE actor = ?" if actor else ""),
                    args[:1] if actor else []).fetchone()[0]
    return {"total": n, "rows": rows}


def counts_for_evidence(con) -> dict:
    """FR-08 counters straight from the database."""
    def one(sql):
        return con.execute(sql).fetchone()[0]
    return {"cylinders": one("SELECT COUNT(*) FROM cylinders"),
            "inspections": one("SELECT COUNT(*) FROM inspections"),
            "inspections_rejected": one("SELECT COUNT(*) FROM inspections WHERE result='reject'"),
            "fillings": one("SELECT COUNT(*) FROM fillings"),
            "fillings_anomalous": one("SELECT COUNT(*) FROM fillings WHERE anomaly != 'ok'"),
            "demand_records": one("SELECT COUNT(*) FROM demand"),
            "rnd_activities": one("SELECT COUNT(*) FROM rnd_activities"),
            "rnd_hours": round(one("SELECT COALESCE(SUM(hours),0) FROM rnd_activities"), 2),
            "rnd_cost": round(one("SELECT COALESCE(SUM(cost),0) FROM rnd_activities"), 2),
            "audit_events": one("SELECT COUNT(*) FROM audit_log")}


def dump_json(detail) -> str:
    return json.dumps(detail, ensure_ascii=False, default=str)
