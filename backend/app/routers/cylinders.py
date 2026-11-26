"""FR-01 cylinder registry, FR-03 digital record counters, FR-04 lifecycle and fleet summary.

D-05 / D-15: a passed requalification is the only API path back to in_service.
D-11: a duplicate barcode is a 409, the same as a duplicate serial.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import require_role, current_user
from ..services.lifecycle import LifecyclePolicy, assess_lifecycle, fleet_summary

router = APIRouter(prefix="/api/cylinders", tags=["cylinders"])


class CylinderIn(BaseModel):
    serial: str = Field(min_length=3, max_length=64)
    barcode: str | None = None
    manufacture_date: date
    last_hydro_test: date | None = None
    region: str | None = None


class RequalIn(BaseModel):
    requalification_date: date
    passed: bool = True
    tare_loss_pct: float | None = Field(default=None, ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)


def _row_or_404(con: Connection, cid: int) -> dict:
    row = con.execute("SELECT * FROM cylinders WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"cylinder {cid} not found")
    return dict(row)


def _with_latest_inspection(con: Connection, cyl: dict) -> dict:
    last = con.execute(
        "SELECT created_at, result, severity FROM inspections WHERE cylinder_id = ? ORDER BY id DESC LIMIT 1",
        (cyl["id"],)).fetchone()
    if last:
        cyl.update(last_inspection_date=last["created_at"][:10], last_inspection_result=last["result"],
                   last_inspection_severity=last["severity"])
    return cyl


def _assess(con: Connection, cyl: dict) -> dict:
    try:
        return assess_lifecycle(_with_latest_inspection(con, cyl))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("", status_code=201)
def create_cylinder(c: CylinderIn, user: dict = Depends(require_role("admin", "inspector")),
                    con: Connection = Depends(get_db)):
    if con.execute("SELECT 1 FROM cylinders WHERE serial = ?", (c.serial,)).fetchone():
        raise HTTPException(409, f"cylinder '{c.serial}' already exists")
    if c.barcode and con.execute("SELECT 1 FROM cylinders WHERE barcode = ?", (c.barcode,)).fetchone():
        raise HTTPException(409, f"barcode '{c.barcode}' already exists")
    try:
        cur = con.execute(
            "INSERT INTO cylinders (serial, barcode, manufacture_date, last_hydro_test, status, region, "
            "fill_count, fail_count, created_at) VALUES (?,?,?,?, 'in_service', ?, 0, 0, ?)",
            (c.serial, c.barcode, c.manufacture_date.isoformat(),
             c.last_hydro_test.isoformat() if c.last_hydro_test else None, c.region, utcnow()))
        con.commit()
    except sqlite3.IntegrityError:
        con.rollback()
        raise HTTPException(409, "serial or barcode already exists") from None
    return {"id": cur.lastrowid, "serial": c.serial, "status": "in_service"}


@router.get("")
def list_cylinders(region: str | None = None, status: str | None = None,
                   limit: int = Query(50, ge=1, le=2000), offset: int = Query(0, ge=0),
                   user: dict = Depends(current_user), con: Connection = Depends(get_db)):
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


@router.get("/fleet/summary")
def fleet(user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    """D-25: fleet counts by lifecycle status and priority, for the quality manager."""
    results = []
    for row in con.execute("SELECT * FROM cylinders ORDER BY id"):
        results.append(_assess(con, dict(row)))
    return fleet_summary(results)


@router.get("/{cid}")
def get_cylinder(cid: int, user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    return _row_or_404(con, cid)


@router.get("/{cid}/lifecycle")
def cylinder_lifecycle(cid: int, user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    """FR-04: requalification schedule, fitness for service and the (uncalibrated) priority index."""
    return _assess(con, _row_or_404(con, cid))


@router.post("/{cid}/requalification", status_code=201)
def requalify(cid: int, body: RequalIn, user: dict = Depends(require_role("admin", "quality")),
              con: Connection = Depends(get_db)):
    """D-05 / D-15: record a requalification and, when it passes, return the cylinder to service."""
    cyl = _row_or_404(con, cid)
    made = date.fromisoformat(str(cyl["manufacture_date"])[:10])
    if body.requalification_date < made:
        raise HTTPException(422, "requalification date is before the manufacture date")
    last = con.execute(
        "SELECT created_at, result FROM inspections WHERE cylinder_id = ? ORDER BY id DESC LIMIT 1",
        (cid,)).fetchone()
    if (body.passed and last and last["result"] == "reject"
            and body.requalification_date.isoformat() <= last["created_at"][:10]):
        raise HTTPException(422, "a passed requalification must be dated after the critical inspection")
    tare_blocks = body.tare_loss_pct is not None and body.tare_loss_pct >= LifecyclePolicy().tare_requalify_loss_pct
    returned = bool(body.passed) and not tare_blocks
    new_status = "in_service" if returned else "rejected"
    reason = body.note or ("requalification passed" if returned else "requalification did not return the cylinder to service")
    if tare_blocks:
        reason = (f"tare loss {body.tare_loss_pct:.1f} % is at or above "
                  f"{LifecyclePolicy().tare_requalify_loss_pct:g} %; cylinder stays out of service")
    con.execute(
        "UPDATE cylinders SET requalification_date = ?, tare_loss_pct = COALESCE(?, tare_loss_pct), "
        "last_hydro_test = CASE WHEN ? THEN ? ELSE last_hydro_test END, status = ? WHERE id = ?",
        (body.requalification_date.isoformat(), body.tare_loss_pct, int(returned),
         body.requalification_date.isoformat(), new_status, cid))
    con.execute(
        "INSERT INTO cylinder_events (cylinder_id, created_at, actor, from_status, to_status, reason) "
        "VALUES (?,?,?,?,?,?)",
        (cid, utcnow(), user["username"], cyl["status"], new_status, reason))
    con.commit()
    return {"id": cid, "status": new_status, "returned_to_service": returned,
            "requalification_date": body.requalification_date.isoformat(), "reason": reason}
