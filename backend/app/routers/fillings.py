"""FR-05 filling control and FR-06 anomaly list."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.db import get_db, utcnow
from ..core.security import require_role, current_user
from ..services.filling import assess_filling, FillPolicy

router = APIRouter(prefix="/api/fillings", tags=["fillings"])


class FillingIn(BaseModel):
    cylinder_id: int
    station_id: str = Field(min_length=1, max_length=64)
    target_weight: float = Field(gt=0)
    actual_weight: float = Field(ge=0)
    leak_signal: bool = False
    rated_capacity: float | None = None
    tare_marked: float | None = None
    tare_measured: float | None = None
    unit: str = "lb"


@router.post("", status_code=201)
def create_filling(f: FillingIn, user: dict = Depends(require_role("admin", "inspector")),
                   con: Connection = Depends(get_db)):
    cyl = con.execute("SELECT * FROM cylinders WHERE id = ?", (f.cylinder_id,)).fetchone()
    if cyl is None:
        raise HTTPException(404, f"cylinder {f.cylinder_id} not found")
    try:
        out = assess_filling(f.target_weight, f.actual_weight, f.leak_signal, f.rated_capacity,
                             f.tare_marked, f.tare_measured, f.unit, FillPolicy())
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    cur = con.execute(
        "INSERT INTO fillings (cylinder_id, station_id, created_at, target_weight, actual_weight, leak_signal, "
        "anomaly, action, detail, actor) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f.cylinder_id, f.station_id, utcnow(), f.target_weight, f.actual_weight, int(f.leak_signal),
         out["anomaly"], out["action"], out["detail"], user["username"]))
    con.execute("UPDATE cylinders SET fill_count = fill_count + 1 WHERE id = ?", (f.cylinder_id,))
    if out["action"] == "reject":                # a rejected filling is a failure of the cylinder (FR-03)
        con.execute("UPDATE cylinders SET fail_count = fail_count + 1, status = 'rejected' WHERE id = ?",
                    (f.cylinder_id,))
    elif out["action"] == "rework" and cyl["status"] == "in_service":
        con.execute("UPDATE cylinders SET status = 'rework' WHERE id = ?", (f.cylinder_id,))
    con.commit()
    return {"id": cur.lastrowid, "cylinder_id": f.cylinder_id, **out}


@router.get("/anomalies")
def anomalies(limit: int = 100, offset: int = 0, user: dict = Depends(current_user),
              con: Connection = Depends(get_db)):
    """FR-06: every filling record whose verdict was not ok, newest first."""
    n = con.execute("SELECT COUNT(*) FROM fillings WHERE anomaly != 'ok'").fetchone()[0]
    rows = [dict(r) for r in con.execute(
        "SELECT f.*, c.serial FROM fillings f JOIN cylinders c ON c.id = f.cylinder_id "
        "WHERE f.anomaly != 'ok' ORDER BY f.id DESC LIMIT ? OFFSET ?", (limit, offset))]
    return {"total": n, "rows": rows}


@router.get("")
def list_fillings(cylinder_id: int | None = None, limit: int = 50, offset: int = 0,
                  user: dict = Depends(current_user), con: Connection = Depends(get_db)):
    q, args = "SELECT * FROM fillings WHERE 1=1", []
    if cylinder_id is not None:
        q += " AND cylinder_id = ?"
        args.append(cylinder_id)
    n = con.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
    return {"total": n, "rows": [dict(r) for r in con.execute(q + " ORDER BY id DESC LIMIT ? OFFSET ?",
                                                              args + [limit, offset])]}
