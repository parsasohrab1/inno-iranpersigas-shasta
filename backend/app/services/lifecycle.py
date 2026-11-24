"""FR-04 lifecycle assessment: requalification schedule and fitness-for-service rules.

Sources of the rules (see docs/DATA_SOURCES.md for the full references):
  * 49 CFR 180.209 (U.S. DOT): requalification period of 4-series cylinders is 5 years; 10 years is
    allowed for certain service when the cylinder is examined internally and externally every 5 years and
    "examined at each filling, and rejected if a dent, corroded area, leak or other condition indicates
    possible weakness"; 12 years (volumetric expansion) for cylinders with an external corrosion-resistant
    coating in non-corrosive gas service.
  * 49 CFR 180.209(c): a 4-series cylinder that shows leak, corrosion, denting, bulging, rough usage or a
    tare loss of 5 % or more must be requalified before it is refilled.
  * Iranian inspection providers state a periodic test every 5 years for LPG cylinders (ISIRI 6792 /
    ISO 18119 and ISIRI 13103 / ISO 11621 are cited for valve and cylinder use).

The schedule rules are deterministic. The `priority_index` is an ordering aid for inspectors (age,
filling intensity, failure history); its weights are engineering judgement and are NOT calibrated to
failure statistics, which the code states in every result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

CRITICAL = ("crack", "valve_damage", "deep_dent", "leak", "bulge", "fire_damage", "requalify_loss")
MINOR = ("rust", "label_missing", "ring_missing", "scratch")
SEVERITY_ORDER = {"none": 0, "minor": 1, "major": 2, "critical": 3}


@dataclass(frozen=True)
class LifecyclePolicy:
    test_interval_years: int = 5                  # 49 CFR 180.209(b) Table 1 / Iranian practice
    extended_interval_years: int | None = None     # 10 (49 CFR 180.209(f)) or 12 (49 CFR 180.209(e)); opt-in
    due_window_days: int = 90
    tare_requalify_loss_pct: float = 5.0           # 49 CFR 180.209(c)
    design_life_years: int | None = None           # operator policy; the sources used prescribe no design life
    retire_after_critical_failures: int | None = None   # operator policy, e.g. 3 confirmed critical findings
    priority_weights: tuple = (0.4, 0.3, 0.3)      # age, filling intensity, failure history (uncalibrated)


def _years(a: date, b: date) -> float:
    return (b - a).days / 365.25


def next_test_date(last_test: date, interval_years: int) -> date:
    """Same calendar day, `interval_years` years later (Feb 29 -> Feb 28)."""
    try:
        return last_test.replace(year=last_test.year + interval_years)
    except ValueError:
        return last_test.replace(year=last_test.year + interval_years, day=28)


def assess_inspection(defects) -> str:
    """MVP rule kept for compatibility: worst defect decides the inspection result."""
    d = {str(x).strip().lower() for x in defects}
    if d & set(CRITICAL):
        return "reject"
    if d & set(MINOR):
        return "review"
    return "pass"


def assess_lifecycle(cyl: dict, today: date | None = None, policy: LifecyclePolicy = LifecyclePolicy()) -> dict:
    """Fitness-for-service and requalification schedule of one cylinder record.

    `cyl` keys: manufacture_date, last_hydro_test (datetime.date or ISO string), fill_count, fail_count,
    last_inspection_date, last_inspection_result (pass|review|reject), last_inspection_severity
    (none|minor|major|critical), tare_loss_pct (optional), status (optional).
    """
    today = today or date.today()

    def d(x):
        return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])

    made, last_test = d(cyl["manufacture_date"]), d(cyl["last_hydro_test"])
    fills = int(cyl.get("fill_count") or 0)
    fails = int(cyl.get("fail_count") or 0)
    insp = str(cyl.get("last_inspection_result") or "pass").lower()
    sev = str(cyl.get("last_inspection_severity") or "none").lower()
    insp_date = d(cyl["last_inspection_date"]) if cyl.get("last_inspection_date") else None
    requal = d(cyl["requalification_date"]) if cyl.get("requalification_date") else None
    tare_loss = float(cyl["tare_loss_pct"]) if cyl.get("tare_loss_pct") not in (None, "") else None

    reasons, status = [], "in_service"
    critical_open = insp == "reject" or SEVERITY_ORDER.get(sev, 0) >= 3
    if critical_open and not (requal and insp_date and requal > insp_date):
        status = "suspend"
        reasons.append("critical finding not yet cleared by a requalification (49 CFR 180.209(c))")
    if tare_loss is not None and tare_loss >= policy.tare_requalify_loss_pct and status != "retire":
        status = "suspend"
        reasons.append(f"tare loss {tare_loss:.1f} % is at or above {policy.tare_requalify_loss_pct:g} % "
                       "(49 CFR 180.209(c))")

    interval = policy.extended_interval_years or policy.test_interval_years
    due = next_test_date(last_test, interval)
    days = (due - today).days
    if days < 0:
        test_state = "overdue"
        reasons.append(f"periodic test overdue by {-days} days")
        if status == "in_service":
            status = "overdue_for_test"
    elif days <= policy.due_window_days:
        test_state = "due"
        reasons.append(f"periodic test due in {days} days")
        if status == "in_service":
            status = "due_for_test"
    else:
        test_state = "valid"

    age = _years(made, today)
    if policy.design_life_years is not None and age >= policy.design_life_years:
        status = "retire"
        reasons.append(f"age {age:.1f} years reached the operator's design life of {policy.design_life_years} years")
    if (policy.retire_after_critical_failures is not None and fails >= policy.retire_after_critical_failures
            and status != "retire"):
        status = "retire"
        reasons.append(f"{fails} confirmed critical findings reached the operator's retirement rule")

    w = policy.priority_weights
    priority = min(1.0, w[0] * age / 15.0 + w[1] * fills / 1000.0 + w[2] * fails / 3.0)
    if status in ("suspend", "retire"):
        priority = 1.0
    elif test_state == "overdue":
        priority = max(priority, 0.8)
    elif test_state == "due":
        priority = max(priority, 0.6)
    level = "high" if priority >= 0.8 else ("medium" if priority >= 0.6 else "low")

    recommendation = {
        "suspend": "do not refill; send for requalification or scrap",
        "retire": "remove from service and scrap",
        "overdue_for_test": "take out of the filling queue and test immediately",
        "due_for_test": "plan the periodic test in the current maintenance window",
    }.get(status, "keep in service; inspect visually at every filling")
    if status == "in_service" and insp == "review":
        recommendation = "keep in service; clear the minor findings at the next inspection"
    return {
        "status": status, "test_state": test_state, "next_test_date": due.isoformat(), "days_to_test": days,
        "test_interval_years": interval, "age_years": round(age, 2), "fill_count": fills, "fail_count": fails,
        "priority_index": round(priority, 3), "priority_level": level,
        "priority_index_note": "inspection-ordering aid with judgement weights; not a calibrated failure probability",
        "recommendation": recommendation, "reasons": reasons,
        "rule_sources": ["49 CFR 180.209(b)-(f)", "NIST SP 2200-01 (tare evidence)"],
    }


def fleet_summary(results: list[dict]) -> dict:
    """Counts by status and priority level for a whole fleet."""
    by = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    pr = {}
    for r in results:
        pr[r["priority_level"]] = pr.get(r["priority_level"], 0) + 1
    overdue = [r["days_to_test"] for r in results if r["test_state"] == "overdue"]
    return {"n": len(results), "by_status": by, "by_priority": pr,
            "share_suspended": round(by.get("suspend", 0) / max(1, len(results)), 4),
            "share_test_overdue": round(len(overdue) / max(1, len(results)), 4),
            "mean_overdue_days": round(sum(-x for x in overdue) / len(overdue), 1) if overdue else 0.0}
