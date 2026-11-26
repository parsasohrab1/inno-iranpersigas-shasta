"""FR-10 audit trail: every API call is recorded with actor, endpoint, status and duration.

D-19: each row carries prev_hash and row_hash (HMAC-SHA256). Editing a stored row breaks
GET /api/audit/verify. D-28: a failed insert is counted and written to stderr; it still must not
break the API response.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import AUDIT_KEY
from .db import connect, utcnow

_dropped = 0


def dropped_count() -> int:
    return _dropped


def chain_hash(prev: str, actor, method: str, path: str, status_code: int,
               duration_ms, detail) -> str:
    payload = "\n".join([
        prev or "",
        "" if actor is None else str(actor),
        str(method),
        str(path),
        str(int(status_code)),
        "" if duration_ms is None else f"{float(duration_ms):.2f}",
        "" if not detail else str(detail),
    ])
    return hmac.new(AUDIT_KEY, payload.encode(), hashlib.sha256).hexdigest()


def record_audit(actor, method: str, path: str, status_code: int, duration_ms, detail) -> None:
    """Append one chained audit row. Failures are counted and never raised."""
    global _dropped
    con = None
    try:
        con = connect()
        con.execute("BEGIN IMMEDIATE")
        last = con.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = "" if last is None or not last["row_hash"] else last["row_hash"]
        digest = chain_hash(prev, actor, method, path, status_code, duration_ms, detail)
        con.execute(
            "INSERT INTO audit_log (created_at, actor, method, path, status_code, duration_ms, detail, "
            "prev_hash, row_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (utcnow(), actor, method, path, int(status_code), duration_ms, detail or None, prev, digest))
        con.commit()
    except Exception as e:  # noqa: BLE001 - the audit DB may not exist yet during early startup
        _dropped += 1
        print(f"lcis audit drop #{_dropped}: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        if con is not None:
            con.close()


def verify_chain(con) -> dict:
    """Recompute the hash chain. A tampered or legacy (unhashed) row fails the check."""
    rows = list(con.execute(
        "SELECT id, actor, method, path, status_code, duration_ms, detail, prev_hash, row_hash "
        "FROM audit_log ORDER BY id"))
    prev = ""
    for row in rows:
        if not row["row_hash"]:
            return {"valid": False, "checked": row["id"] - 1 if rows else 0, "broken_at": row["id"],
                    "reason": "row has no hash"}
        expect_prev = prev
        digest = chain_hash(expect_prev, row["actor"], row["method"], row["path"], row["status_code"],
                            row["duration_ms"], row["detail"])
        if (row["prev_hash"] or "") != expect_prev or row["row_hash"] != digest:
            return {"valid": False, "checked": row["id"], "broken_at": row["id"],
                    "reason": "hash does not match the stored row"}
        prev = row["row_hash"]
    return {"valid": True, "checked": len(rows), "broken_at": None, "reason": None}


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
            record_audit(actor, request.method, request.url.path, status_code,
                         round((time.perf_counter() - t0) * 1000, 2), detail or None)
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
            "audit_events": one("SELECT COUNT(*) FROM audit_log"),
            "vision_verdicts": one("SELECT COUNT(*) FROM vision_verdicts")}


def dump_json(detail) -> str:
    return json.dumps(detail, ensure_ascii=False, default=str)
