#!/usr/bin/env python
"""FR-03 / FR-04 / FR-05 rule engines: boundary tests and a replay of the published NIST statistics.

  python -I validate_rules.py <product_dir> [--lang fa|en]

A. Boundary cases of the filling engine (every threshold and every precedence rule).
B. Lot-acceptance cases of the NIST Handbook 133 procedure.
C. Boundary cases of the lifecycle engine (schedule, suspension, retirement, policy variants).
D. Replays against NIST SP 2200-01:
     D1 tare tiers of 9,482 new and 822 used cylinders versus Table 2 (individual records are not
        published, so individual deviations are simulated inside the published bands);
     D2 lot verdicts and overfill severity of the Phase III exchange survey (959 lots, 10,456 cylinders):
        a population with the published verdict shares and overfill bins is generated and the engine is
        run on it; its verdicts must also equal an independent statement of the rule on every lot;
     D3 bands of product remaining in 6,896 returned cylinders.
Writes validation/results/rules_validation.json and validation/figures/rules_replay.png.
"""
import json
import os
import sys
from datetime import date

import numpy as np

sys.path.insert(0, os.path.join(sys.argv[1], "backend"))
from app.services.filling import FillPolicy, assess_filling, assess_lot, assess_tare, return_band  # noqa: E402
from app.services.lifecycle import LifecyclePolicy, assess_lifecycle, fleet_summary  # noqa: E402

TODAY = date(2026, 11, 24)


# ----------------------------------------------------------------------------- A
def filling_cases():
    return [
        ("exact fill", dict(target_weight=15, actual_weight=15.0), "ok", "accept"),
        ("inside MAV", dict(target_weight=15, actual_weight=14.80), "ok", "accept"),
        ("shortage equal to MAV is not an error", dict(target_weight=15, actual_weight=14.72), "ok", "accept"),
        ("shortage just over MAV", dict(target_weight=15, actual_weight=14.71), "short_fill", "rework"),
        ("over safe fill by 2.5 %", dict(target_weight=20, actual_weight=20.5, rated_capacity=20), "overfill", "reject"),
        ("over safe fill by 7.5 %", dict(target_weight=20, actual_weight=21.5, rated_capacity=20), "overfill", "reject"),
        ("at safe fill is fine", dict(target_weight=20, actual_weight=20.0, rated_capacity=20), "ok", "accept"),
        ("partial fill of a larger cylinder", dict(target_weight=10, actual_weight=10.2, rated_capacity=20), "ok", "accept"),
        ("no capacity: +19 % passes the heuristic", dict(target_weight=10, actual_weight=11.9), "ok", "accept"),
        ("no capacity: +21 % flagged", dict(target_weight=10, actual_weight=12.1), "overfill", "review"),
        ("leak outranks everything", dict(target_weight=15, actual_weight=14.0, leak_signal=True), "leak", "reject"),
        ("tare loss 5.6 %", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=17.0), "tare_loss", "reject"),
        ("tare loss exactly 5 %", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=17.1), "tare_loss", "reject"),
        ("tare loss 4.4 % is non-compliant but not a reject", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=17.2),
         "tare_out_of_tolerance", "review"),
        ("tare 2.2 % light: inside DOT", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=17.6), "ok", "accept"),
        ("tare 0.28 % light: inside HB 130", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=17.95), "ok", "accept"),
        ("tare 2.8 % heavy is outside +1 %", dict(target_weight=15, actual_weight=15.0, tare_marked=18, tare_measured=18.5),
         "tare_out_of_tolerance", "review"),
        ("short fill outranks a tare review", dict(target_weight=15, actual_weight=14.0, tare_marked=18, tare_measured=18.5),
         "short_fill", "rework"),
        ("40 lb cylinder: -2.33 % is outside -2 %", dict(target_weight=35, actual_weight=35.0, rated_capacity=40, tare_marked=30, tare_measured=29.3),
         "tare_out_of_tolerance", "review"),
        ("20 lb cylinder: -2.33 % is inside -3 %", dict(target_weight=15, actual_weight=15.0, rated_capacity=20, tare_marked=18, tare_measured=17.58),
         "ok", "accept"),
        ("kg: MAV scales with the label", dict(target_weight=10, actual_weight=9.50, unit="kg"), "short_fill", "rework"),
        ("kg: 10 kg cylinder (22 lb) uses the -3 % band", dict(target_weight=10, actual_weight=10.0, unit="kg", rated_capacity=10, tare_marked=15, tare_measured=14.55),
         "ok", "accept"),
        ("kg: just beyond -3 %", dict(target_weight=10, actual_weight=10.0, unit="kg", rated_capacity=10, tare_marked=15, tare_measured=14.54),
         "tare_out_of_tolerance", "review"),
        ("kg: 17 kg cylinder (37 lb) uses the -2 % band", dict(target_weight=15, actual_weight=15.0, unit="kg", rated_capacity=17, tare_marked=18, tare_measured=17.5),
         "tare_out_of_tolerance", "review"),
        ("scale tolerance widens the band", dict(target_weight=15, actual_weight=14.68, policy=FillPolicy(scale_tolerance=0.05)), "ok", "accept"),
        ("scale tolerance is not unlimited", dict(target_weight=15, actual_weight=14.66, policy=FillPolicy(scale_tolerance=0.05)), "short_fill", "rework"),
        ("explicit MAV of 0.5", dict(target_weight=20, actual_weight=19.6, policy=FillPolicy(mav=0.5)), "ok", "accept"),
    ]


def run_filling():
    out = []
    for name, kw, anomaly, action in filling_cases():
        r = assess_filling(**kw)
        out.append({"case": name, "expected_anomaly": anomaly, "expected_action": action,
                    "got_anomaly": r["anomaly"], "got_action": r["action"],
                    "pass": r["anomaly"] == anomaly and r["action"] == action, "detail": r["detail"]})
    o = assess_filling(20, 21.5, rated_capacity=20)
    out.append({"case": "overfill severity band 5-10 %", "expected_anomaly": "5_to_10_pct", "expected_action": "",
                "got_anomaly": o.get("overfill_severity", ""), "got_action": "",
                "pass": o.get("overfill_severity") == "5_to_10_pct"})
    t = assess_tare(18, 17.0, capacity=20)
    out.append({"case": "tare tier of a 5.6 % loss", "expected_anomaly": "requalify", "expected_action": "",
                "got_anomaly": t["tier"], "got_action": "", "pass": t["tier"] == "requalify"})
    return out


# ----------------------------------------------------------------------------- B
def run_lots():
    lot = [0.05, -0.02, 0.10, -0.05, 0.0, 0.08, -0.01, 0.03, 0.12, -0.04, 0.02, 0.01]
    cases = [
        ("accurate lot passes", list(lot), "pass", True),
        ("one cylinder 0.29 lb short fails the MAV only", [-0.29] + lot[1:], "mav_only", False),
        ("average -0.10 with SEL 0.05 fails the average only", [-0.10] * 12, "average_only", False),
        ("both requirements fail", [-0.40] + [-0.10] * 11, "both", False),
    ]
    out = []
    for name, errors, category, passes in cases:
        r = assess_lot(errors, 0.28)
        out.append({"case": name, "expected": category, "got": r["category"], "pass": r["category"] == category and r["passes"] == passes,
                    "average_error": round(r["average_error"], 4), "sel": round(r["sel"], 4)})
    try:
        assess_lot([0.0] * 10, 0.28)
        raised = False
    except ValueError:
        raised = True
    out.append({"case": "n = 10 without a sample correction factor is refused", "expected": "ValueError",
                "got": "ValueError" if raised else "no error", "pass": raised})
    r = assess_lot([0.0] * 10, 0.28, scf=0.7)
    out.append({"case": "n = 10 with an explicit factor works", "expected": "pass", "got": r["category"], "pass": r["category"] == "pass"})
    return out


# ----------------------------------------------------------------------------- C
def cyl(**kw):
    base = dict(manufacture_date="2020-03-15", last_hydro_test="2024-06-01", fill_count=120, fail_count=0,
                last_inspection_date="2026-05-10", last_inspection_result="pass", last_inspection_severity="none")
    base.update(kw)
    return base


def lifecycle_cases():
    p5 = LifecyclePolicy()
    return [
        ("valid test, in service", cyl(), p5, "in_service", "2029-06-01"),
        ("inside the 90-day window", cyl(last_hydro_test="2021-12-01"), p5, "due_for_test", "2026-12-01"),
        ("overdue by 176 days", cyl(last_hydro_test="2021-06-01"), p5, "overdue_for_test", "2026-06-01"),
        ("10-year interval when the service qualifies", cyl(), LifecyclePolicy(extended_interval_years=10), "in_service", "2034-06-01"),
        ("12-year interval for coated cylinders", cyl(), LifecyclePolicy(extended_interval_years=12), "in_service", "2036-06-01"),
        ("29 February rounds to 28", cyl(last_hydro_test="2024-02-29"), p5, "in_service", "2029-02-28"),
        ("critical finding suspends the cylinder", cyl(last_inspection_result="reject", last_inspection_severity="critical"), p5, "suspend", "2029-06-01"),
        ("requalification after the finding clears it", cyl(last_inspection_result="reject", last_inspection_severity="critical",
                                                           requalification_date="2026-08-20"), p5, "in_service", "2029-06-01"),
        ("requalification before the finding does not clear it", cyl(last_hydro_test="2025-08-20", last_inspection_result="reject",
                                                                    last_inspection_severity="critical",
                                                                    requalification_date="2025-08-20"), p5, "suspend", "2030-08-20"),
        ("tare loss of 5.0 % suspends", cyl(tare_loss_pct=5.0), p5, "suspend", "2029-06-01"),
        ("tare loss of 4.9 % does not suspend", cyl(tare_loss_pct=4.9), p5, "in_service", "2029-06-01"),
        ("minor findings keep the cylinder in service", cyl(last_inspection_result="review", last_inspection_severity="minor"), p5, "in_service", "2029-06-01"),
        ("operator design life reached", cyl(manufacture_date="1993-01-01"), LifecyclePolicy(design_life_years=30), "retire", "2029-06-01"),
        ("operator retirement rule on repeated critical findings",
         cyl(fail_count=3), LifecyclePolicy(retire_after_critical_failures=3), "retire", "2029-06-01"),
        ("suspension outranks an overdue test", cyl(last_hydro_test="2020-01-01", tare_loss_pct=6.0), p5, "suspend", "2025-01-01"),
    ]


def run_lifecycle():
    out = []
    for name, c, policy, status, due in lifecycle_cases():
        r = assess_lifecycle(c, TODAY, policy)
        out.append({"case": name, "expected_status": status, "expected_next_test": due, "got_status": r["status"],
                    "got_next_test": r["next_test_date"], "days_to_test": r["days_to_test"],
                    "pass": r["status"] == status and r["next_test_date"] == due})
    return out


# ----------------------------------------------------------------------------- D1
def tare_replay(seed=0):
    rng = np.random.default_rng(seed)

    def inside(lo, hi, n):
        u = rng.random(n)
        return lo + (hi - lo) * u

    specs = {
        "new": {"n": 9482, "hb130": 44.3, "dot_light": 48.8, "dot_heavy": 5.3, "outside": 1.6},
        "used": {"n": 822, "hb130": 32.0, "dot_light": 17.6, "dot_heavy": 16.1, "outside": 34.3},
    }
    out = {}
    for cls, s in specs.items():
        n = s["n"]
        names = ["hb130", "dot_light", "dot_heavy", "outside"]
        counts = [int(round(n * s[k] / 100.0)) for k in names]   # exactly the published number of cylinders per band
        counts[-1] += n - sum(counts)
        band = np.repeat(np.array(names), counts)
        rng.shuffle(band)
        dev = np.empty(n)
        for name, (lo_b, hi_b) in (("hb130", (-0.5, 0.5)), ("dot_light", (-3.0, -0.5)), ("dot_heavy", (0.5, 1.0))):
            m = band == name
            dev[m] = inside(lo_b, hi_b, m.sum())
        m = band == "outside"          # not published: half above +1 %, half lighter than -3 % (some >= 5 %)
        idx = np.where(m)[0]
        half = len(idx) // 2
        dev[idx[:half]] = inside(1.0, 3.0, half)
        dev[idx[half:]] = inside(-8.0, -3.0, len(idx) - half)
        marked = np.full(n, 18.0)
        measured = marked * (1 + dev / 100.0)
        tiers = np.array([assess_tare(18.0, float(x), capacity=20)["tier"] for x in measured])
        pub = {"hb130": s["hb130"], "dot": s["dot_light"] + s["dot_heavy"], "outside_dot": s["outside"]}
        got = {"hb130": 100 * float(np.mean(tiers == "hb130")), "dot": 100 * float(np.mean(tiers == "dot")),
               "outside_dot": 100 * float(np.mean(np.isin(tiers, ["noncompliant", "requalify"])))}
        ok = {k: abs(pub[k] - got[k]) <= 0.6 for k in pub}   # rounding of the band counts only
        out[cls] = {"n": n, "published_pct": pub, "replayed_pct": {k: round(v, 2) for k, v in got.items()},
                    "within_0_6_pp": ok, "pass": all(ok.values()),
                    "requalify_share_pct_not_published": round(100 * float(np.mean(tiers == "requalify")), 2)}
    return out


# ----------------------------------------------------------------------------- D2
PUBLISHED_LOTS = {"pass": 74.2, "mav_only": 17.1, "average_only": 1.2, "both": 7.5}
PUBLISHED_OVERFILL = {"(0,1]": 91, "(1,2]": 10, "(2,3]": 7, "(3,4]": 5, ">4": 5}
N_LOTS, N_PER_LOT, N_CYL_PUBLISHED = 959, 12, 10456
OVER_EDGES = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 6)]


def verdict_formula(e):
    """Independent (vectorised) statement of the NIST Handbook 133 rule used to build the population."""
    avg, sd = e.mean(), e.std(ddof=1)
    avg_ok = avg >= 0 or avg >= -0.635 * sd
    mav_ok = bool((e >= -0.28).all())
    return ("pass" if mav_ok else "mav_only") if avg_ok else ("average_only" if mav_ok else "both")


def one_lot(rng, lot_type):
    """Cylinder errors of one location of the given verdict type; resampled until the rule returns that verdict."""
    n = N_PER_LOT
    for _ in range(200):
        e = -np.abs(rng.normal(0.0, 0.02, n))              # filled to the label, never above it
        if lot_type in ("pass", "mav_only"):
            short = rng.random(n) < 0.15
            e = np.where(short, rng.uniform(-0.27, -0.03, n), e)
        if lot_type == "mav_only":
            idx = rng.choice(n, size=1 if rng.random() < 0.8 else 2, replace=False)
            e[idx] = rng.uniform(-1.0, -0.31, len(idx))      # gross shortage -> MAV failure
        elif lot_type == "average_only":
            e = rng.uniform(-0.275, -0.12, n)                # every cylinder moderately short, none beyond MAV
        elif lot_type == "both":
            idx = rng.choice(n, size=int(rng.integers(4, 10)), replace=False)
            e[idx] = rng.uniform(-0.60, -0.31, len(idx))     # many cylinders beyond MAV
        if verdict_formula(e) == lot_type:
            return e
    raise RuntimeError(f"could not build a lot of type {lot_type}")


def build_population(seed):
    """959 lots whose verdict types follow the published shares, then overfills on 118 / 10,456 of the cylinders."""
    rng = np.random.default_rng(seed)
    types = list(PUBLISHED_LOTS)
    lots = rng.choice(types, size=N_LOTS, p=[PUBLISHED_LOTS[t] / 100 for t in types])
    e = np.array([one_lot(rng, t) for t in lots])
    over_rate = sum(PUBLISHED_OVERFILL.values()) / N_CYL_PUBLISHED
    over = rng.random(e.shape) < over_rate                    # separate overfill process, applied afterwards
    counts = np.array(list(PUBLISHED_OVERFILL.values()))
    b = rng.choice(len(OVER_EDGES), size=int(over.sum()), p=counts / counts.sum())
    e[over] = rng.uniform(np.array([lo for lo, _ in OVER_EDGES])[b], np.array([hi for _, hi in OVER_EDGES])[b])
    return e


def fill_replay(seeds=(11, 12, 13)):
    runs = []
    for seed in seeds:
        e = build_population(seed)
        anomalies = [assess_filling(20.0, 20.0 + float(x), rated_capacity=20.0) for x in e.ravel()]
        lots = [assess_lot([float(x) for x in row], 0.28) for row in e]
        cat = {k: 100 * float(np.mean([l["category"] == k for l in lots])) for k in PUBLISHED_LOTS}
        agree = all(l["category"] == verdict_formula(row) for l, row in zip(lots, e))
        over = np.maximum(e.ravel(), 0)
        bins = [int(np.sum((over > a) & (over <= b))) for a, b in OVER_EDGES]
        runs.append({"lots_pct": cat, "engine_equals_formula": agree, "cylinders": int(e.size),
                     "overfill_pct": 100 * float(np.mean([a["anomaly"] == "overfill" for a in anomalies])),
                     "short_fill_pct": 100 * float(np.mean([a["anomaly"] == "short_fill" for a in anomalies])),
                     "overfill_bins": dict(zip(PUBLISHED_OVERFILL, bins)),
                     "overfill_severity_pct": {k: 100 * float(np.mean([a.get("overfill_severity") == k for a in anomalies]))
                                               for k in ("up_to_5_pct", "5_to_10_pct", "10_to_15_pct", "15_to_20_pct", "over_20_pct")}})
    mean = lambda f: float(np.mean([f(r) for r in runs]))  # noqa: E731
    pub_bins_pct = {k: 100 * v / N_CYL_PUBLISHED for k, v in PUBLISHED_OVERFILL.items()}
    got_bins_pct = {k: mean(lambda r, k=k: r["overfill_bins"][k]) / N_LOTS / N_PER_LOT * 100 for k in PUBLISHED_OVERFILL}
    return {"population_generator": "constructive: 959 lots drawn with the published verdict shares; cylinder errors "
                                    "resampled until the NIST rule yields that verdict; overfills added with the published "
                                    "rate and severity bins (individual NIST records are not published)",
            "lots_simulated": N_LOTS, "sample_per_lot": N_PER_LOT,
            "lots_verdict_pct": {k: round(mean(lambda r, k=k: r["lots_pct"][k]), 2) for k in PUBLISHED_LOTS},
            "published_lots_pct": PUBLISHED_LOTS,
            "within_3_pp": {k: abs(mean(lambda r, k=k: r["lots_pct"][k]) - PUBLISHED_LOTS[k]) <= 3.0 for k in PUBLISHED_LOTS},
            "engine_equals_independent_formula": all(r["engine_equals_formula"] for r in runs),
            "overfill_pct": round(mean(lambda r: r["overfill_pct"]), 2),
            "published_overfill_pct": round(100 * 118 / N_CYL_PUBLISHED, 2),
            "overfill_bins_pct": {k: round(v, 3) for k, v in got_bins_pct.items()},
            "published_overfill_bins_pct": {k: round(v, 3) for k, v in pub_bins_pct.items()},
            "overfill_bins_within_0_1_pp": {k: abs(got_bins_pct[k] - pub_bins_pct[k]) <= 0.1 for k in PUBLISHED_OVERFILL},
            "short_fill_pct": round(mean(lambda r: r["short_fill_pct"]), 2), "seeds": list(seeds)}


# ----------------------------------------------------------------------------- D3
def return_replay(seed=0):
    rng = np.random.default_rng(seed)
    n = 6896
    pub = {"0 lb or negative": 15.9, "up to 1 lb": 58.8, "1 lb to 3 lbs": 10.6, "3 lbs to 5 lbs": 5.6, "over 5 lbs": 9.1}
    band = rng.choice(list(pub), size=n, p=[v / 100 for v in pub.values()])
    remaining = np.empty(n)
    span = {"0 lb or negative": (-0.5, 0.0), "up to 1 lb": (0.0, 1.0), "1 lb to 3 lbs": (1.0, 3.0),
            "3 lbs to 5 lbs": (3.0, 5.0), "over 5 lbs": (5.0, 12.0)}
    for k, (lo, hi) in span.items():
        m = band == k
        remaining[m] = rng.uniform(lo, hi, m.sum())
    got = {k: 100 * float(np.mean([return_band(float(x)) == k for x in remaining])) for k in pub}
    ok = {k: abs(pub[k] - got[k]) <= 1.0 for k in pub}
    return {"n": n, "published_pct": pub, "replayed_pct": {k: round(v, 2) for k, v in got.items()},
            "within_1_pp": ok, "pass": all(ok.values()),
            "published_more_than_1lb_pct": 25.4, "replayed_more_than_1lb_pct": round(100 - got["0 lb or negative"] - got["up to 1 lb"], 2)}


def main():
    product = sys.argv[1]
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "fa"
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    res = {"module": "FR-03 / FR-04 / FR-05 rule engines", "reference_statistics": "NIST SP 2200-01 (2022)",
           "filling_boundary": run_filling(), "lot_acceptance": run_lots(), "lifecycle_boundary": run_lifecycle(),
           "tare_replay": tare_replay(), "return_product_replay": return_replay()}
    res["fill_replay"] = fill_replay()
    groups = {"filling_boundary": "filling_boundary", "lot_acceptance": "lot_acceptance", "lifecycle_boundary": "lifecycle_boundary"}
    for k in groups:
        res[k + "_summary"] = {"cases": len(res[k]), "passed": sum(r["pass"] for r in res[k])}
    res["pass"] = (all(res[k + "_summary"]["passed"] == res[k + "_summary"]["cases"] for k in groups)
                   and res["tare_replay"]["new"]["pass"] and res["tare_replay"]["used"]["pass"]
                   and res["return_product_replay"]["pass"]
                   and all(res["fill_replay"]["within_3_pp"].values())
                   and res["fill_replay"]["engine_equals_independent_formula"]
                   and all(res["fill_replay"]["overfill_bins_within_0_1_pp"].values()))
    os.makedirs(os.path.join(product, "validation/results"), exist_ok=True)
    os.makedirs(os.path.join(product, "validation/figures"), exist_ok=True)
    with open(os.path.join(product, "validation/results/rules_validation.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    for k in groups:
        for r in res[k]:
            if not r["pass"]:
                print("  FAIL", k, r)
    for k in groups:
        print(f"{k}: {res[k + '_summary']['passed']}/{res[k + '_summary']['cases']} cases pass")
    for cls in ("new", "used"):
        print(f"tare replay {cls}: published {res['tare_replay'][cls]['published_pct']} replayed {res['tare_replay'][cls]['replayed_pct']} pass={res['tare_replay'][cls]['pass']}")
    fr = res["fill_replay"]
    print("fill replay lots %:", fr["lots_verdict_pct"], "published:", fr["published_lots_pct"], fr["within_3_pp"])
    print("engine == independent formula on every lot:", fr["engine_equals_independent_formula"])
    print("overfill %:", fr["overfill_pct"], "published:", fr["published_overfill_pct"])
    print("overfill bins %:", fr["overfill_bins_pct"], "published:", fr["published_overfill_bins_pct"], fr["overfill_bins_within_0_1_pp"])
    print("short-fill (MAV) share of cylinders %:", fr["short_fill_pct"])
    print("return replay:", res["return_product_replay"]["replayed_pct"], "pass =", res["return_product_replay"]["pass"])
    print("OVERALL PASS:", res["pass"])

    from plotutil import save, setup
    plt, T = setup(lang)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    keys = list(PUBLISHED_LOTS)
    x = np.arange(len(keys))
    axes[0].bar(x - 0.2, [PUBLISHED_LOTS[k] for k in keys], 0.4, label=T("گزارش NIST"), color="#94a3b8")
    axes[0].bar(x + 0.2, [fr["lots_verdict_pct"][k] for k in keys], 0.4, label=T("بازپخش با موتور قواعد"), color="#2563eb")
    axes[0].set_xticks(x, [T({"pass": "قبول", "mav_only": "فقط MAV", "average_only": "فقط میانگین", "both": "هر دو"}[k]) for k in keys], fontsize=9)
    axes[0].set_title(T("رأی لات‌ها (۹۵۹ لات، فاز سوم)"))
    axes[0].set_ylabel(T("درصد"))
    axes[0].legend(frameon=False, fontsize=8)
    t = res["tare_replay"]
    x = np.arange(2)
    for i, (k, lab) in enumerate((("hb130", "±۰٫۵٪ (HB130)"), ("dot", "مجاز DOT"), ("outside_dot", "خارج از مجاز DOT"))):
        axes[1].bar(x + (i - 1) * 0.26, [t["new"]["replayed_pct"][k], t["used"]["replayed_pct"][k]], 0.26, label=T(lab))
    axes[1].set_xticks(x, [T("سیلندرهای نو (۹٬۴۸۲)"), T("سیلندرهای کارکرده (۸۲۲)")], fontsize=9)
    axes[1].set_title(T("انطباق وزن خالی با تلورانس"))
    axes[1].legend(frameon=False, fontsize=8)
    rp = res["return_product_replay"]
    keys = list(rp["published_pct"])
    x = np.arange(len(keys))
    axes[2].bar(x - 0.2, [rp["published_pct"][k] for k in keys], 0.4, color="#94a3b8", label=T("گزارش NIST"))
    axes[2].bar(x + 0.2, [rp["replayed_pct"][k] for k in keys], 0.4, color="#f59e0b", label=T("بازپخش"))
    axes[2].set_xticks(x, [T({"0 lb or negative": "صفر یا منفی", "up to 1 lb": "تا ۱ پوند", "1 lb to 3 lbs": "۱ تا ۳ پوند",
                              "3 lbs to 5 lbs": "۳ تا ۵ پوند", "over 5 lbs": "بیش از ۵ پوند"}[k]) for k in keys], fontsize=8)
    axes[2].set_title(T("گاز باقی‌مانده در سیلندرهای بازگشتی (۶٬۸۹۶)"))
    axes[2].legend(frameon=False, fontsize=8)
    for a in axes:
        a.grid(axis="y", alpha=0.3)
    save(fig, os.path.join(product, "validation/figures/rules_replay.png"))


if __name__ == "__main__":
    main()
