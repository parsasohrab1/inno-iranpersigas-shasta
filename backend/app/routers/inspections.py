"""FR-02 visual inspection registration with the SRS verdict rule; FR-03 counters."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import require_role, current_user
from ..services.lifecycle import assess_inspection, CRITICAL, MINOR

router = APIRouter(prefix="/api/inspections", tags=["inspections"])

KNOWN_DEFECTS = sorted(set(CRITICAL) | set(MINOR))


class DefectIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    note: str | None = None


class InspectionIn(BaseModel):
    cylinder_id: int
    defects: list[DefectIn] = []
    image_url: str | None = None


@router.post("", status_code=201)
def create_inspection(i: InspectionIn, user: dict = Depends(require_role("admin", "inspector", "quality")),
                      con: Connection = Depends(get_db)):
    cyl = con.execute("SELECT * FROM cylinders WHERE id = ?", (i.cylinder_id,)).fetchone()
    if cyl is None:
        raise HTTPException(404, f"cylinder {i.cylinder_id} not found")
    codes = [d.code.strip().lower() for d in i.defects]
    unknown = [c for c in codes if c not in KNOWN_DEFECTS]
    if unknown:
        raise HTTPException(422, f"unknown defect codes {unknown}; known: {KNOWN_DEFECTS}")
    result = assess_inspection(codes)
    severity = {"reject": "critical", "review": "minor", "pass": "none"}[result]
    now = utcnow()
    cur = con.execute(
        "INSERT INTO inspections (cylinder_id, created_at, defects_json, result, severity, image_url, actor) "
        "VALUES (?,?,?,?,?,?,?)",
        (i.cylinder_id, now, json.dumps([{"code": c, "note": d.note} for c, d in zip(codes, i.defects)],
                                        ensure_ascii=False), result, severity, i.image_url, user["username"]))
    if result == "reject":                       # FR-03: a rejected inspection counts as a failure
        con.execute("UPDATE cylinders SET fail_count = fail_count + 1, last_inspection_date = ?, "
                    "last_inspection_result = ?, last_inspection_severity = ? WHERE id = ?",
                    (now[:10], result, severity, i.cylinder_id))
    else:
        con.execute("UPDATE cylinders SET last_inspection_date = ?, last_inspection_result = ?, "
                    "last_inspection_severity = ? WHERE id = ?", (now[:10], result, severity, i.cylinder_id))
    con.commit()
    return {"id": cur.lastrowid, "cylinder_id": i.cylinder_id, "result": result, "severity": severity,
            "defects": codes}


@router.get("")
def list_inspections(cylinder_id: int | None = None, limit: int = 50, offset: int = 0,
                     user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    q, args = "SELECT * FROM inspections WHERE 1=1", []
    if cylinder_id is not None:
        q += " AND cylinder_id = ?"
        args.append(cylinder_id)
    n = con.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
    rows = [dict(r) for r in con.execute(q + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset])]
    for r in rows:
        r["defects"] = json.loads(r["defects_json"])
        del r["defects_json"]
    return {"total": n, "rows": rows}
