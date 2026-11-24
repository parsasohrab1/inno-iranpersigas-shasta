"""FR-08 R&D evidence and FR-10 audit trail endpoints."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlite3 import Connection

from ..core.audit import audit_query, counts_for_evidence
from ..core.config import ML_DIR, PRODUCT_VERSION, VALIDATION_DIR
from ..core.db import get_db, utcnow
from ..core.security import current_user, require_role
from ..services import vision

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


class ActivityIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    hours: float = Field(ge=0, le=10000)
    cost: float = Field(default=0, ge=0)
    note: str | None = None


def _validation_summary() -> dict:
    out = {}
    for name in ("rules_validation.json", "forecast_backtest.json", "forecast_ensemble.json",
                 "forecast_auto_select.json", "vision_validation.json"):
        p = os.path.join(VALIDATION_DIR, name)
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                if name == "rules_validation.json":
                    out["rules"] = {"pass": d.get("pass"),
                                    "filling": d.get("filling_boundary_summary"),
                                    "lot": d.get("lot_acceptance_summary"),
                                    "lifecycle": d.get("lifecycle_boundary_summary")}
                elif name == "forecast_backtest.json":
                    w = d.get("weekly_national", {})
                    out["forecast"] = {"selected": w.get("selected"),
                                       "policy_equals_best": w.get("policy_equals_best_on_backtest"),
                                       "skill_vs_mvp": (w.get("skill_vs_mvp_moving_average", {})
                                                        .get(w.get("selected", ""), {}).get("skill"))}
                elif name == "vision_validation.json":
                    out["vision"] = {"test_accuracy": d.get("test_accuracy"),
                                     "verdict_summary": d.get("verdict_summary"),
                                     "n_train": d.get("n_train"), "n_test": d.get("n_test")}
                else:
                    out[name.replace(".json", "")] = d
            except Exception as e:  # noqa: BLE001
                out[name] = f"unreadable: {e}"
    return out


@router.get("/rnd")
def rnd_evidence(user: dict = Depends(require_role("admin", "auditor", "quality")),
                 con: Connection = Depends(get_db)):
    hashes = {}
    for name in ("forecast_model.json", "vision_model.json"):
        p = os.path.join(ML_DIR, name)
        if os.path.exists(p):
            from ..services.vision import file_sha256
            hashes[name] = file_sha256(p)
    return {"product_version": PRODUCT_VERSION, "counters": counts_for_evidence(con),
            "validation": _validation_summary(), "artefact_sha256": hashes,
            "note": "validation numbers are produced by pipelines/ (run_validation.py) and are reproducible "
                    "from the committed data + code; hours/costs come from the R&D activity log"}


@router.post("/rnd/activities", status_code=201)
def add_activity(a: ActivityIn, user: dict = Depends(require_role("admin", "quality", "auditor")),
                 con: Connection = Depends(get_db)):
    cur = con.execute("INSERT INTO rnd_activities (created_at, title, hours, cost, note, actor) "
                      "VALUES (?,?,?,?,?,?)", (utcnow(), a.title, a.hours, a.cost, a.note, user["username"]))
    con.commit()
    return {"id": cur.lastrowid, **a.model_dump()}


@router.get("/rnd/activities")
def list_activities(limit: int = Query(100, le=500), user: dict = Depends(current_user),
                    con: Connection = Depends(get_db)):
    rows = [dict(r) for r in con.execute("SELECT * FROM rnd_activities ORDER BY id DESC LIMIT ?", (limit,))]
    return {"total": len(rows), "rows": rows}


audit_router = APIRouter(prefix="/api/audit", tags=["audit"])


@audit_router.get("")
def read_audit(limit: int = Query(50, le=500), offset: int = 0, actor: str | None = None,
               user: dict = Depends(require_role("admin", "auditor")), con: Connection = Depends(get_db)):
    """FR-10: the audit trail. Only the system manager and the auditor may read it."""
    if limit > 2000:
        raise HTTPException(422, "limit too large")
    return audit_query(con, limit, offset, actor)


@audit_router.get("/summary")
def audit_summary(user: dict = Depends(require_role("admin", "auditor")), con: Connection = Depends(get_db)):
    return {"total": con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            "by_actor": [dict(r) for r in con.execute(
                "SELECT actor, COUNT(*) AS n FROM audit_log GROUP BY actor ORDER BY n DESC LIMIT 20")],
            "slowest_ms": con.execute("SELECT MAX(duration_ms) FROM audit_log").fetchone()[0],
            "mean_ms": con.execute("SELECT AVG(duration_ms) FROM audit_log").fetchone()[0]}
