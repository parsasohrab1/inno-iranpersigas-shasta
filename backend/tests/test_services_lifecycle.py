"""Unit tests of the lifecycle service (FR-04): schedule, suspension, retirement, priority index."""
from datetime import date

from app.services.lifecycle import (LifecyclePolicy, assess_inspection, assess_lifecycle, fleet_summary,
                                    next_test_date)

CYL = {"manufacture_date": "2020-06-01", "last_hydro_test": "2024-06-01", "fill_count": 100, "fail_count": 0}


def test_valid_in_service():
    r = assess_lifecycle(CYL, today=date(2025, 6, 1))
    assert r["status"] == "in_service" and r["test_state"] == "valid" and r["priority_level"] == "low"


def test_due_and_overdue():
    r = assess_lifecycle(CYL, today=date(2029, 5, 1))          # 30 days before 2029-06-01
    assert r["test_state"] == "due" and r["status"] == "due_for_test"
    r = assess_lifecycle(CYL, today=date(2030, 1, 1))
    assert r["test_state"] == "overdue" and r["status"] == "overdue_for_test"
    assert r["priority_index"] >= 0.8


def test_suspend_on_rejected_inspection():
    c = {**CYL, "last_inspection_date": "2025-01-10", "last_inspection_result": "reject",
         "last_inspection_severity": "critical"}
    r = assess_lifecycle(c, today=date(2025, 6, 1))
    assert r["status"] == "suspend" and r["priority_index"] == 1.0
    assert any("requalification" in x for x in r["reasons"])


def test_requalification_clears_suspension():
    c = {**CYL, "last_inspection_date": "2025-01-10", "last_inspection_result": "reject",
         "last_inspection_severity": "critical", "requalification_date": "2025-02-01"}
    assert assess_lifecycle(c, today=date(2025, 6, 1))["status"] == "in_service"


def test_tare_loss_suspends():
    c = {**CYL, "tare_loss_pct": 6.0}
    r = assess_lifecycle(c, today=date(2025, 6, 1))
    assert r["status"] == "suspend" and any("tare loss" in x for x in r["reasons"])


def test_design_life_retirement():
    r = assess_lifecycle(CYL, today=date(2040, 1, 1), policy=LifecyclePolicy(design_life_years=15))
    assert r["status"] == "retire"
    assert r["recommendation"] == "remove from service and scrap"
    assert any("design life" in x for x in r["reasons"])


def test_retire_after_critical_failures():
    c = {**CYL, "fail_count": 3}
    r = assess_lifecycle(c, today=date(2025, 6, 1), policy=LifecyclePolicy(retire_after_critical_failures=3))
    assert r["status"] == "retire"


def test_extended_interval():
    r = assess_lifecycle(CYL, today=date(2029, 6, 1), policy=LifecyclePolicy(extended_interval_years=10))
    assert r["test_state"] == "valid" and r["test_interval_years"] == 10


def test_priority_index_is_uncalibrated_aid():
    c = {**CYL, "fill_count": 900, "fail_count": 2}
    r = assess_lifecycle(c, today=date(2025, 6, 1))
    assert 0.0 <= r["priority_index"] <= 1.0 and "not a calibrated" in r["priority_index_note"]


def test_fleet_summary():
    res = [assess_lifecycle(CYL, today=date(2025, 6, 1)), assess_lifecycle(CYL, today=date(2030, 1, 1))]
    s = fleet_summary(res)
    assert s["n"] == 2 and s["share_test_overdue"] == 0.5 and "overdue_for_test" in s["by_status"]


def test_next_test_date_feb29():
    assert next_test_date(date(2024, 2, 29), 5) == date(2029, 2, 28)


def test_missing_hydro_test_is_overdue_not_an_error():
    c = {"manufacture_date": "2010-01-01", "last_hydro_test": None, "fill_count": 0, "fail_count": 0}
    r = assess_lifecycle(c, today=date(2026, 1, 1))
    assert r["test_state"] == "overdue" and r["status"] == "overdue_for_test"
    assert any("no periodic test" in x for x in r["reasons"])


def test_assess_inspection_verdict_rule():
    assert assess_inspection(["crack"]) == "reject"
    assert assess_inspection(["valve_damage", "rust"]) == "reject"
    assert assess_inspection(["rust", "scratch"]) == "review"
    assert assess_inspection([]) == "pass"
    assert assess_inspection(["RUST"]) == "review"              # case-insensitive
