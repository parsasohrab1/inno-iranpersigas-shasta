#!/usr/bin/env python
"""Convert the downloaded raw files into clean, documented CSV datasets inside the product package.

  python -I build_datasets.py <raw_eia_dir> <nist_txt> <product_data_dir>

Inputs (untrusted, read only):  EIA *.xls workbooks, text of NIST SP 2200-01 (pdftotext -layout output)
Outputs: data/eia/*.csv, data/nist/*.csv, data/sources.json
"""
import hashlib
import json
import os
import re
import sys

import pandas as pd

EIA = {
    "weekly": {
        "file": "WPRUP_NUS_2w.xls", "key": "WPRUP_NUS_2", "sheet": "Data 1",
        "url": "https://www.eia.gov/dnav/pet/hist_xls/WPRUP_NUS_2w.xls",
        "name": "Weekly U.S. Product Supplied of Propane and Propylene", "unit": "thousand barrels per day",
    },
    "monthly": {
        "R10": "East Coast (PADD 1)", "R20": "Midwest (PADD 2)", "R30": "Gulf Coast (PADD 3)",
        "R40": "Rocky Mountain (PADD 4)", "R50": "West Coast (PADD 5)",
    },
}
RETRIEVED = "2026-11-24"


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def eia_weekly(raw_dir, out_dir, sources):
    cfg = EIA["weekly"]
    p = os.path.join(raw_dir, cfg["file"])
    df = pd.read_excel(p, sheet_name=cfg["sheet"], skiprows=[0, 1])
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.dropna().sort_values("date").reset_index(drop=True)
    assert df["date"].is_unique and df["value"].notna().all()
    out = os.path.join(out_dir, "eia_weekly_us_propane_propylene_product_supplied.csv")
    df.to_csv(out, index=False, date_format="%Y-%m-%d")
    sources.append({"dataset": os.path.basename(out), "publisher": "U.S. Energy Information Administration",
                    "url": cfg["url"], "series": cfg["key"], "name": cfg["name"], "unit": cfg["unit"],
                    "frequency": "weekly", "retrieved": RETRIEVED, "raw_sha256": sha256(p),
                    "rows": int(len(df)), "start": df.date.min().strftime("%Y-%m-%d"),
                    "end": df.date.max().strftime("%Y-%m-%d"), "licence": "U.S. Government public information"})
    print(f"[eia] weekly: {len(df)} rows {df.date.min().date()} -> {df.date.max().date()}")


def eia_monthly(raw_dir, out_dir, sources):
    rows = []
    for code, region in EIA["monthly"].items():
        p = os.path.join(raw_dir, f"PET_CONS_PSUP_DC_{code}_MBBLPD_M.xls")
        xl = pd.ExcelFile(p)
        raw = xl.parse("Data 1", header=None)
        keys = raw.iloc[1].tolist()
        dates = pd.to_datetime(raw.iloc[3:, 0]).dt.to_period("M").dt.to_timestamp()
        for j, k in enumerate(keys):
            if not isinstance(k, str):
                continue
            series = None
            if k.startswith("M_EPLLPA_VPP_") and k.endswith("_MBBLD"):
                series = "propane"
            elif re.fullmatch(r"MPRUPP\d+", k):
                series = "propane_and_propylene"
            if series is None:
                continue
            v = pd.to_numeric(raw.iloc[3:, j], errors="coerce")
            m = v.notna()
            for d, x in zip(dates[m], v[m]):
                rows.append({"series": series, "region": region, "date": d.strftime("%Y-%m-%d"), "value": float(x)})
        sources.append({"dataset": f"PET_CONS_PSUP_DC_{code}_MBBLPD_M.xls",
                        "publisher": "U.S. Energy Information Administration",
                        "url": f"https://www.eia.gov/dnav/pet/xls/PET_CONS_PSUP_DC_{code}_MBBLPD_M.xls",
                        "region": region, "unit": "thousand barrels per day", "frequency": "monthly",
                        "retrieved": RETRIEVED, "raw_sha256": sha256(p), "licence": "U.S. Government public information"})
    df = pd.DataFrame(rows).sort_values(["series", "region", "date"]).reset_index(drop=True)
    out = os.path.join(out_dir, "eia_monthly_padd_product_supplied.csv")
    df.to_csv(out, index=False)
    print("[eia] monthly:", df.groupby(["series", "region"]).agg(n=("value", "size"), first=("date", "min"),
                                                            last=("date", "max")).to_string())


def parse_code_table(lines):
    """Lines look like 'CODE count' or 'CODE count   CODE count' (two-column layout of the PDF)."""
    pairs = []
    for ln in lines:
        if re.search(r"^(NIST SP 2200-01|October 2022)\s*$", ln):  # running page header
            continue
        t = ln.split()
        if len(t) == 2 and t[1].isdigit():
            pairs.append((t[0], int(t[1])))
        elif len(t) == 4 and t[1].isdigit() and t[3].isdigit():
            pairs += [(t[0], int(t[1])), (t[2], int(t[3]))]
    return pairs


def nist(txt, out_dir, sources):
    lines = open(txt, encoding="utf-8", errors="replace").read().splitlines()
    def block(start_pat, stop_pat):
        s = next(i for i, l in enumerate(lines) if re.search(start_pat, l))
        e = next(i for i in range(s + 1, len(lines)) if re.search(stop_pat, lines[i]))
        return lines[s + 1:e]
    d1 = parse_code_table(block(r"^Table D1\.", r"^Table D2\."))
    d2 = parse_code_table(block(r"^Table D2\.", r"^Appendix E\."))
    # sanity: reported totals in the report text
    tot = {"new": 9482, "used": 822}
    s1, s2 = sum(c for _, c in d1), sum(c for _, c in d2)
    print(f"[nist] D1 codes={len(d1)} sum={s1} (report total {tot['new']}, diff {s1 - tot['new']})")
    print(f"[nist] D2 codes={len(d2)} sum={s2} (report total {tot['used']}, diff {s2 - tot['used']})")
    codes = pd.DataFrame(
        [{"cylinder_class": cls, "manufacturer_code": c, "count": n} for cls, tab in (("new", d1), ("used", d2))
         for c, n in tab])
    codes["report_total"] = codes["cylinder_class"].map(tot)
    codes.to_csv(os.path.join(out_dir, "nist_sp2200-01_manufacturer_codes.csv"), index=False)

    bins = pd.DataFrame([
        {"metric": "cylinders_over_safe_fill", "group": ">20 to 21 lb", "count": 91},
        {"metric": "cylinders_over_safe_fill", "group": ">21 to 22 lb", "count": 10},
        {"metric": "cylinders_over_safe_fill", "group": ">22 to 23 lb", "count": 7},
        {"metric": "cylinders_over_safe_fill", "group": ">23 to 24 lb", "count": 5},
        {"metric": "cylinders_over_safe_fill", "group": ">24 lb", "count": 5},
    ])
    bins.to_csv(os.path.join(out_dir, "nist_sp2200-01_overfill_severity.csv"), index=False)

    S = [
        # metric, group, value, unit, where in NIST SP 2200-01
        ("tare_within_tolerance", "new|(-0.5 to 0)%", 29.7, "percent", "Table 2"),
        ("tare_within_tolerance", "new|(0 to +0.5)%", 14.6, "percent", "Table 2"),
        ("tare_within_tolerance", "new|HB130 total", 44.3, "percent", "Table 2"),
        ("tare_within_tolerance", "new|(-3 to 0)%", 78.5, "percent", "Table 2"),
        ("tare_within_tolerance", "new|(0 to +1)%", 19.9, "percent", "Table 2"),
        ("tare_within_tolerance", "new|DOT total", 98.4, "percent", "Table 2"),
        ("tare_within_tolerance", "used|(-0.5 to 0)%", 16.1, "percent", "Table 2"),
        ("tare_within_tolerance", "used|(0 to +0.5)%", 15.9, "percent", "Table 2"),
        ("tare_within_tolerance", "used|HB130 total", 32.0, "percent", "Table 2"),
        ("tare_within_tolerance", "used|(-3 to 0)%", 33.7, "percent", "Table 2"),
        ("tare_within_tolerance", "used|(0 to +1)%", 32.0, "percent", "Table 2"),
        ("tare_within_tolerance", "used|DOT total", 65.7, "percent", "Table 2"),
        ("cylinders_tested", "new", 9482, "count", "Phase I text"),
        ("lots_tested", "new", 702, "count", "Phase I text"),
        ("states", "phase1_new", 19, "count", "Phase I text"),
        ("cylinders_tested", "used", 822, "count", "Phase I text"),
        ("lots_tested", "used", 71, "count", "Phase I text"),
        ("states", "phase1_used", 8, "count", "Phase I text"),
        ("lots_with_negative_mean_error", "new", 607, "count", "Phase I text"),
        ("lots_with_negative_mean_error_median_pct", "new", -0.54, "percent", "Phase I text"),
        ("lots_with_positive_mean_error", "new", 92, "count", "Phase I text"),
        ("lots_with_positive_mean_error_median_pct", "new", 0.23, "percent", "Phase I text"),
        ("lots_with_zero_mean_error", "new", 3, "count", "Phase I text"),
        ("lots_with_positive_mean_error", "used", 69, "count", "Phase I text"),
        ("lots_with_positive_mean_error_median_pct", "used", 0.82, "percent", "Phase I text"),
        ("lots_with_negative_mean_error", "used", 2, "count", "Phase I text"),
        ("lots_with_negative_mean_error_median_pct", "used", -0.03, "percent", "Phase I text"),
        ("individual_cylinders_below_dot_lower_limit", "new", 21, "count", "Phase I text"),
        ("individual_cylinders_above_dot_upper_limit", "new", 222, "count", "Phase I text"),
        ("individual_cylinders_below_dot_lower_limit", "used", 21, "count", "Phase I text"),
        ("tare_weight_pct_lower_dot", "cylinders_25lb_or_less", -3.0, "percent", "DOT Final Rule discussion"),
        ("tare_weight_pct_upper_dot", "cylinders_25lb_or_less", 1.0, "percent", "DOT Final Rule discussion"),
        ("tare_weight_pct_lower_dot", "cylinders_over_25lb", -2.0, "percent", "DOT Final Rule discussion"),
        ("tare_weight_pct_upper_dot", "cylinders_over_25lb", 1.0, "percent", "DOT Final Rule discussion"),
        ("mav_example", "labeled_net_15lb", 0.28, "lb", "Appendix A"),
        ("refilling_locations_inspected", "phase2", 1559, "count", "Phase II text"),
        ("states", "phase2", 18, "count", "Phase II text"),
        ("method_of_sale", "weight_only", 11.0, "percent", "Table 3"),
        ("method_of_sale", "volume_only", 53.6, "percent", "Table 3"),
        ("method_of_sale", "weight_and_volume", 2.2, "percent", "Table 3"),
        ("method_of_sale", "flat_fee_only", 24.7, "percent", "Table 3"),
        ("method_of_sale", "weight_or_volume_with_flat_fee", 8.5, "percent", "Table 3"),
        ("fill_level_verification", "weight_only", 26.2, "percent", "Table 4"),
        ("fill_level_verification", "bleeder_valve_only", 33.0, "percent", "Table 4"),
        ("fill_level_verification", "opd_only", 10.0, "percent", "Table 4"),
        ("fill_level_verification", "combination", 30.8, "percent", "Table 4"),
        ("exchange_locations_inspected", "phase3", 959, "count", "Phase III text"),
        ("states", "phase3", 17, "count", "Phase III text"),
        ("cylinders_tested", "phase3", 10456, "count", "Phase III text"),
        ("lots_passed", "phase3", 74.2, "percent", "Phase III text"),
        ("lots_failed_mav_only", "phase3", 17.1, "percent", "Phase III text"),
        ("lots_failed_average_only", "phase3", 1.2, "percent", "Phase III text"),
        ("lots_failed_both", "phase3", 7.5, "percent", "Phase III text"),
        ("cylinders_over_safe_fill", "phase3_total", 118, "count", "Phase III text"),
        ("cylinders_returned_tested", "phase3", 6896, "count", "Phase III text"),
        ("product_remaining", "0 lb or negative", 15.9, "percent", "Phase III text"),
        ("product_remaining", "up to 1 lb", 58.8, "percent", "Phase III text"),
        ("product_remaining", "1 lb to 3 lbs", 10.6, "percent", "Phase III text"),
        ("product_remaining", "3 lbs to 5 lbs", 5.6, "percent", "Phase III text"),
        ("product_remaining", "over 5 lbs", 9.1, "percent", "Phase III text"),
        ("product_remaining", "returned_more_than_1lb", 25.4, "percent", "Abstract / Phase III text"),
    ]
    sm = pd.DataFrame(S, columns=["metric", "group", "value", "unit", "source_location"])
    sm.insert(0, "document", "NIST SP 2200-01 (2022)")
    sm.to_csv(os.path.join(out_dir, "nist_sp2200-01_summary.csv"), index=False)
    print(f"[nist] summary rows: {len(sm)}")
    sources.append({"dataset": "NIST SP 2200-01", "publisher": "National Institute of Standards and Technology",
                    "title": "2022 NCWM-NIST National Survey on 20 lb LPG (Propane) Cylinders",
                    "url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.2200-01.pdf",
                    "doi": "10.6028/NIST.SP.2200-01", "retrieved": RETRIEVED, "raw_sha256": sha256(txt),
                    "licence": "U.S. Government public information (NIST)",
                    "note": "Table 2 prints n = 1,535 for used cylinders while the abstract and Phase I text state 822; "
                            "this package uses 822 (Phase I text) and transcribes Table 2 percentages as printed. "
                            "Table D1 (new cylinders) lists 45 manufacturer codes whose counts sum to 9,492, i.e. 10 more "
                            "than the 9,482 total stated in the Phase I text; the table is transcribed as printed. "
                            "Table D2 (used cylinders) sums exactly to 822."})


def main():
    raw_dir, txt, out_dir = sys.argv[1:4]
    sources = []
    os.makedirs(os.path.join(out_dir, "eia"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "nist"), exist_ok=True)
    eia_weekly(raw_dir, os.path.join(out_dir, "eia"), sources)
    eia_monthly(raw_dir, os.path.join(out_dir, "eia"), sources)
    nist(txt, os.path.join(out_dir, "nist"), sources)
    with open(os.path.join(out_dir, "sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=1, ensure_ascii=False)
    print("[ok] wrote", os.path.join(out_dir, "sources.json"))


if __name__ == "__main__":
    main()
