"""FR-01 cylinder registry, FR-03 digital record counters, FR-04 lifecycle endpoint."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import require_role, current_user
from ..services.lifecycle import assess_lifecycle

router = APIRouter(prefix="/api/cylinders", tags=["cylinders"])


class CylinderIn(BaseModel):
    serial: str = Field(min_length=3, max_length=64)
    barcode: str | None = None
    manufacture_date: date
    last_hydro_test: date | None = None
    region: str | None = None


@router.post("", status_code=201)
def create_cylinder(c: CylinderIn, user: dict = Depends(require_role("admin", "inspector")),
                    con: Connection = Depends(get_db)):
    if con.execute("SELECT 1 FROM cylinders WHERE serial = ?", (c.serial,)).fetchone():
        raise HTTPException(409, f"cylinder '{c.serial}' already exists")
    cur = con.execute(
        "INSERT INTO cylinders (serial, barcode, manufacture_date, last_hydro_test, status, region, "
        "fill_count, fail_count, created_at) VALUES (?,?,?,?, 'in_service', ?, 0, 0, ?)",
        (c.serial, c.barcode, c.manufacture_date.isoformat(),
         c.last_hydro_test.isoformat() if c.last_hydro_test else None, c.region, utcnow()))
    con.commit()
    return {"id": cur.lastrowid, "serial": c.serial, "status": "in_service"}


def _row_or_404(con: Connection, cid: int) -> dict:
    row = con.execute("SELECT * FROM cylinders WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"cylinder {cid} not found")
    return dict(row)


@router.get("")
def list_cylinders(region: str | None = None, status: str | None = None, limit: int = Query(50, le=2000),
                   offset: int = 0, user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    q, args = "SELECT * FROM cylinders WHERE 1=1", []
    if region:
        q += " AND region = ?"
        args.append(region)
    if status:
        q += " AND status = ?"
        args.append(status)
    n = con.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
    rows = [dict(r) for r in con.execute(q + " ORDER BY id LIMIT ? OFFSET ?", args + [limit, offset])]
    return {"total": n, "rows": rows}


@router.get("/{cid}")
def get_cylinder(cid: int, user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    return _row_or_404(con, cid)


@router.get("/{cid}/lifecycle")
def cylinder_lifecycle(cid: int, user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    """FR-04: requalification schedule, fitness for service and the (uncalibrated) priority index."""
    c = _row_or_404(con, cid)
    last = con.execute("SELECT created_at, result, severity FROM inspections WHERE cylinder_id = ? "
                       "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    if last:
        c.update(last_inspection_date=last["created_at"][:10], last_inspection_result=last["result"],
                 last_inspection_severity=last["severity"])
    return assess_lifecycle(c)
