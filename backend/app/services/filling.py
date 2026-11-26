"""FR-05 filling control and FR-03 net-quantity checks: a rule engine with traceable tolerances.

Every threshold is a parameter with a published source, so the engineering team can change it when a
regulation changes without touching the logic:

  tare tolerance of cylinders up to 25 lb (49 CFR 178.35, as surveyed in NIST SP 2200-01) : -3 % ... +1 %
  tare tolerance of cylinders over 25 lb (same rule)                                          : -2 % ... +1 %
  tare loss that requires requalification before refilling (49 CFR 180.209(c))               : 5 % or more
  tare tolerance of NIST Handbook 130 (the tighter weights-and-measures limit)               : +/- 0.5 %
  maximum allowable variation (MAV) of a labelled net weight (NIST HB 133, App. A, Tab. 2-5,
      quoted in NIST SP 2200-01 for 14.3 lb ... 17.7 lb labelled)                            : 0.28 lb
  lot acceptance, NIST HB 133 Category A plan as implemented in NIST SP 2200-01              : n = 12, SCF = 0.635
  safe fill level (NIST SP 2200-01 Phase III)                                                : rated capacity of the cylinder

All weights are expressed in one unit (lb or kg) that the caller states; percentages are unit-free.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

LB_PER_KG = 2.2046226218
EPS = 1e-9   # comparisons at an exact threshold must not depend on floating-point rounding
ANOMALIES = ("ok", "leak", "tare_loss", "short_fill", "overfill", "tare_out_of_tolerance")
SCF = {12: 0.635}          # sample correction factor of the Category A sampling plan (NIST HB 133)
MAV_EXAMPLE = (15.0, 0.28)  # NIST SP 2200-01, Appendix A: labelled 15 lb -> MAV 0.28 lb
# The same appendix quotes 0.28 lb across the 14.3 lb ... 17.7 lb labelled band (not a linear scale).
MAV_BAND_LB = (14.3, 17.7, 0.28)


@dataclass(frozen=True)
class FillPolicy:
    mav: float | None = None              # MAV in weight units; None -> labelled weight x (0.28 / 15)
    tare_requalify_loss_pct: float = 5.0  # 49 CFR 180.209(c)
    hb130_tare_pct: float = 0.5           # NIST Handbook 130
    dot_lower_pct: float = -3.0           # 49 CFR 178.35, cylinders <= 25 lb
    dot_upper_pct: float = 1.0
    dot_lower_pct_over_25lb: float = -2.0
    dot_upper_pct_over_25lb: float = 1.0
    scale_tolerance: float = 0.0          # scale division / uncertainty in weight units (widens acceptance)
    legacy_overfill_fraction: float = 0.2  # first-SRS heuristic, used only when no rated capacity is given


def tare_band(capacity: float | None = None, policy: FillPolicy = FillPolicy()) -> tuple[float, float]:
    """Allowed tare deviation in percent for a cylinder of the given capacity (lb; None -> up to 25 lb)."""
    if capacity is not None and capacity > 25.0:
        return policy.dot_lower_pct_over_25lb, policy.dot_upper_pct_over_25lb
    return policy.dot_lower_pct, policy.dot_upper_pct


def assess_tare(marked: float, measured: float, capacity: float | None = None, unit: str = "lb",
                policy: FillPolicy = FillPolicy()) -> dict:
    """Compare the measured tare weight with the marked (stamped) tare weight.

    Returned tier: 'requalify' (loss >= 5 %), 'noncompliant' (outside the DOT band),
    'dot' (inside the DOT band but outside +/- 0.5 %) or 'hb130' (inside +/- 0.5 %).
    """
    if marked <= 0:
        raise ValueError("marked tare weight must be positive")
    dev_pct = (measured - marked) / marked * 100.0        # negative = cylinder lighter than its stamp
    loss_pct = -dev_pct
    lo, hi = tare_band(capacity_lb(capacity, unit), policy)
    if loss_pct >= policy.tare_requalify_loss_pct - EPS:
        tier, severity, action = "requalify", "critical", "reject"
        source = "49 CFR 180.209(c): a 4-series cylinder with 5 % or more tare loss must be requalified before refilling"
    elif dev_pct < lo - EPS or dev_pct > hi + EPS:
        tier, severity, action = "noncompliant", "major", "review"
        source = f"49 CFR 178.35 tare tolerance {lo:g} % ... {hi:g} %"
    elif abs(dev_pct) <= policy.hb130_tare_pct + EPS:
        tier, severity, action = "hb130", "none", "accept"
        source = "NIST Handbook 130 tare tolerance +/- 0.5 %"
    else:
        tier, severity, action = "dot", "minor", "accept"
        source = f"within 49 CFR 178.35 ({lo:g} % ... {hi:g} %) but outside NIST HB 130 (+/- 0.5 %)"
    return {"tier": tier, "deviation_pct": dev_pct, "loss_pct": loss_pct, "severity": severity, "action": action,
            "source": source}


def mav_for(target_weight: float, unit: str = "lb", policy: FillPolicy = FillPolicy()) -> tuple[float, str]:
    """MAV in the caller's unit, and where the number came from.

    Inside the band quoted by NIST SP 2200-01 Appendix A the MAV is the published 0.28 lb.
    Outside that band the linear scale of the 15 lb example is used and labelled as an approximation
    (D-26): Handbook 133 Table 2-5 is not linear, and this repository only cites the one band.
    """
    if policy.mav is not None:
        return float(policy.mav), "explicit"
    lo, hi, mav_lb = MAV_BAND_LB
    if unit == "lb" and lo - EPS <= target_weight <= hi + EPS:
        return mav_lb, "nist_sp2200-01 appendix A (14.3 lb to 17.7 lb)"
    return target_weight * MAV_EXAMPLE[1] / MAV_EXAMPLE[0], "linear approximation of the 15 lb example"


def capacity_lb(capacity: float | None, unit: str) -> float | None:
    if capacity is None:
        return None
    return float(capacity) * LB_PER_KG if unit == "kg" else float(capacity)


def overfill_severity(over: float, capacity: float) -> str:
    """Severity band of an overfill, following the bands reported in NIST SP 2200-01 (Fig. 10)."""
    pct = over / capacity * 100.0
    if pct <= 5.0:
        return "up_to_5_pct"
    if pct <= 10.0:
        return "5_to_10_pct"
    if pct <= 15.0:
        return "10_to_15_pct"
    if pct <= 20.0:
        return "15_to_20_pct"
    return "over_20_pct"


def assess_filling(target_weight: float, actual_weight: float, leak_signal: bool = False,
                   rated_capacity: float | None = None, tare_marked: float | None = None,
                   tare_measured: float | None = None, unit: str = "lb",
                   policy: FillPolicy = FillPolicy()) -> dict:
    """Classify one filling record.

    Order of precedence: leak > tare loss >= 5 % > short fill > overfill > tare out of tolerance > ok.
    Actions: reject (do not release the cylinder), rework (add or remove product), review (quality
    staff decides), accept.
    """
    if target_weight <= 0 or actual_weight < 0:
        raise ValueError("weights must be positive")
    tol = policy.scale_tolerance
    mav, mav_source = mav_for(target_weight, unit, policy)
    checks = [{"check": "net_quantity", "deviation": round(actual_weight - target_weight, 4),
               "mav": round(mav, 4), "mav_source": mav_source}]
    out = {"anomaly": "ok", "severity": "none", "action": "accept", "unit": unit, "checks": checks, "detail": ""}
    if leak_signal:
        out.update(anomaly="leak", severity="critical", action="reject",
                   detail="leak signal during or after filling")
        checks.append({"check": "leak", "status": "fail", "source": "49 CFR 180.209(c): a cylinder that shows leak must be requalified before refilling"})
    elif tare_marked is not None and tare_measured is not None:
        tare = assess_tare(tare_marked, tare_measured, rated_capacity, unit, policy)
        checks.append({"check": "tare", **{k: tare[k] for k in ("tier", "deviation_pct", "source")}})
        if tare["tier"] == "requalify":
            out.update(anomaly="tare_loss", severity="critical", action="reject", detail=tare["source"])
        elif tare["tier"] == "noncompliant":
            out.update(anomaly="tare_out_of_tolerance", severity="major", action="review", detail=tare["source"])
    if out["anomaly"] in ("ok", "tare_out_of_tolerance"):
        dev = actual_weight - target_weight
        capacity = rated_capacity
        if dev < -mav - tol - EPS:
            out.update(anomaly="short_fill", severity="major", action="rework",
                       detail=f"shortage {abs(dev):.3f} {unit} exceeds the MAV {mav:.3f} {unit} "
                              "(NIST HB 133 unreasonable minus error)")
            checks[0]["status"] = "fail_mav"
        elif capacity is not None and actual_weight > capacity + tol + EPS:
            out.update(anomaly="overfill", severity="critical", action="reject",
                       detail=f"{actual_weight - capacity:.3f} {unit} above the safe fill level ({capacity:g} {unit})",
                       overfill_severity=overfill_severity(actual_weight - capacity, capacity))
            checks[0]["status"] = "fail_safe_fill"
        elif capacity is None and dev > policy.legacy_overfill_fraction * target_weight + tol + EPS:
            out.update(anomaly="overfill", severity="major", action="review",
                       detail="no rated capacity given; first-SRS heuristic (+20 % of the target) applied")
            checks[0]["status"] = "fail_heuristic"
        elif out["anomaly"] == "ok":
            checks[0]["status"] = "pass"
            if dev < 0:
                out["detail"] = f"below label by {abs(dev):.3f} {unit}, within the MAV {mav:.3f} {unit}"
    return out


def assess_lot(errors: list[float], mav: float, scf: float | None = None) -> dict:
    """Lot acceptance of NIST Handbook 133 as implemented in NIST SP 2200-01.

    Average requirement: mean error >= 0, or mean error >= -SEL with SEL = sample standard deviation x SCF.
    Individual package requirement: no package error may be below -MAV.
    """
    n = len(errors)
    if n < 2:
        raise ValueError("a lot needs at least two package errors")
    if scf is None:
        if n not in SCF:
            raise ValueError(f"no sample correction factor for n = {n}; pass scf explicitly")
        scf = SCF[n]
    avg = statistics.fmean(errors)
    sel = statistics.stdev(errors) * scf
    avg_ok = avg >= 0 or avg >= -sel
    mav_ok = all(e >= -mav for e in errors)
    category = ("pass" if mav_ok else "mav_only") if avg_ok else ("average_only" if mav_ok else "both")
    return {"n": n, "average_error": avg, "sel": sel, "average_requirement_met": avg_ok,
            "mav_requirement_met": mav_ok, "passes": avg_ok and mav_ok, "category": category}


RETURN_BANDS = ("0 lb or negative", "up to 1 lb", "1 lb to 3 lbs", "3 lbs to 5 lbs", "over 5 lbs")


def return_band(remaining: float) -> str:
    """Band of product left in a returned cylinder (NIST SP 2200-01 Phase III bands)."""
    if remaining <= 0:
        return RETURN_BANDS[0]
    if remaining <= 1:
        return RETURN_BANDS[1]
    if remaining <= 3:
        return RETURN_BANDS[2]
    if remaining <= 5:
        return RETURN_BANDS[3]
    return RETURN_BANDS[4]
