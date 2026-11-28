"""Dashboard bundle: synthetic operations plus measured algorithm and test evidence.

Algorithm rows are read from validation/results. They are not recomputed from the
synthetic fleet. Test coverage is read from the snapshot written by
tools/refresh_coverage.py; a missing snapshot is reported, not invented.
"""
from __future__ import annotations

import json
import os

from ..core.config import PRODUCT_VERSION, VALIDATION_DIR
from .synthetic_ops import build_operations

_CACHE: dict | None = None


def _load(name: str) -> dict:
    path = os.path.join(VALIDATION_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _technologies() -> list[dict]:
    rules = _load("rules_validation.json")
    vision = _load("vision_validation.json")
    forecast = _load("forecast_backtest.json")
    auto = _load("forecast_auto_select.json")
    weekly = forecast["weekly_national"]
    models = weekly["models"]
    skill = weekly["skill_vs_mvp_moving_average"]
    filling_ok = rules["filling_boundary_summary"]["passed"]
    filling_n = rules["filling_boundary_summary"]["cases"]
    lot_ok = rules["lot_acceptance_summary"]["passed"]
    lot_n = rules["lot_acceptance_summary"]["cases"]
    life_ok = rules["lifecycle_boundary_summary"]["passed"]
    life_n = rules["lifecycle_boundary_summary"]["cases"]
    replay = rules["fill_replay"]
    order = ("ridge", "gbm", "ensemble_seasonal_ridge", "moving_average_14d", "seasonal_naive", "seasonal_drift", "naive")
    names = {
        "ridge": "رگرسیون ریج با جمله‌های فوریه",
        "gbm": "درخت تقویت‌شده (HistGradientBoosting)",
        "ensemble_seasonal_ridge": "میانگین فصلی‌ساده، رانش فصلی و ریج",
        "moving_average_14d": "میانگین ۱۴ نقطه (خط پایهٔ MVP)",
        "seasonal_naive": "فصلی ساده",
        "seasonal_drift": "رانش فصلی",
        "naive": "تکرار آخرین مشاهده",
    }
    rows = [
        {
            "module": "بازرسی ظاهری",
            "name": "رگرسیون لجستیک چندکلاسه روی ویژگی رنگ، بافت و HOG",
            "metric": "دقت آزمون",
            "value": f"{vision['test_accuracy'] * 100:.2f}٪",
            "detail": (f"آموزش {vision['split']['train']} / آزمون {vision['split']['test']}؛ "
                       f"خلوص حکم pass {vision['verdict_summary']['pass_purity'] * 100:.2f}٪؛ "
                       f"آستانهٔ pass {vision['thresholds']['pass_p_min']}"),
            "source": "validation/results/vision_validation.json",
        },
        {
            "module": "بازرسی ظاهری",
            "name": "قاعدهٔ حکم FR-02 (بحرانی رد، جزئی بازبینی، سالمِ مطمئن قبول)",
            "metric": "حکم‌های آزمون",
            "value": (f"قبول {vision['verdict_summary']['pass']} / "
                      f"بازبینی {vision['verdict_summary']['review']} / "
                      f"رد {vision['verdict_summary']['reject']}"),
            "detail": vision["label_caveat"],
            "source": "validation/results/vision_validation.json",
        },
        {
            "module": "چرخهٔ عمر",
            "name": "قاعدهٔ آزمون دوره‌ای ۴۹ CFR 180.209",
            "metric": "آزمون مرزی",
            "value": f"{life_ok} از {life_n}",
            "detail": "فاصلهٔ ۵ سال، تعلیق پس از یافتهٔ بحرانی، افت تار ۵٪",
            "source": "validation/results/rules_validation.json",
        },
        {
            "module": "کنترل پر کردن",
            "name": "موتور پرکنندگی (MAV هندبوک ۱۳۳، سطح پر شدن ایمن، نشتی و تار)",
            "metric": "آزمون مرزی",
            "value": f"{filling_ok} از {filling_n}",
            "detail": "آستانه‌ها به NIST SP 2200-01 و ۴۹ CFR ردیابی می‌شوند",
            "source": "validation/results/rules_validation.json",
        },
        {
            "module": "کنترل پر کردن",
            "name": "پذیرش لات NIST Handbook 133",
            "metric": "آزمون مرزی",
            "value": f"{lot_ok} از {lot_n}",
            "detail": (f"بازپخش لات‌های پذیرفته {replay['lots_verdict_pct']['pass']}٪ "
                       f"در برابر منتشرشده {replay['published_lots_pct']['pass']}٪"),
            "source": "validation/results/rules_validation.json",
        },
    ]
    for kind in order:
        if kind not in models:
            continue
        h1 = models[kind]["h1"]
        sk = skill[kind]["skill"]
        rows.append({
            "module": "پیش‌بینی تقاضا",
            "name": names.get(kind, kind),
            "metric": "RMSE یک‌گام / مهارت در برابر میانگین ۱۴",
            "value": f"{h1['rmse']:.1f} / {sk:+.2f}",
            "detail": (f"MAPE {h1['mape']:.2f}٪ · پوشش بازهٔ ۸۰٪ یک‌گام "
                       f"{models[kind].get('coverage80_h1', 0):.2f}"
                       + (" · روش انتخاب‌شدهٔ سیاست" if kind == weekly.get("selected") else "")),
            "source": "validation/results/forecast_backtest.json",
        })
    tol = auto["parsimony_policy"]["0.05"]
    rows.append({
        "module": "پیش‌بینی تقاضا",
        "name": "سیاست انتخاب روش بر حسب تعداد فصل",
        "metric": "برابری با بهترین، تلورانس ۵٪",
        "value": f"{tol['n_equal_best']} از {auto['n_series']}",
        "detail": f"میانگین نقص {tol['mean_regret_pct']:.2f}٪ · سیاست روی سری ملی: {weekly['selected']}",
        "source": "validation/results/forecast_auto_select.json",
    })
    rows.append({
        "module": "بستر",
        "name": "JWT (HS256) و pbkdf2-hmac-sha256 برای رمز",
        "metric": "نقش‌ها",
        "value": "۶ نقش",
        "detail": "قفل ورود پس از ۵ شکست؛ خروج نسخهٔ توکن را بالا می‌برد",
        "source": "backend/app/core/security.py",
    })
    rows.append({
        "module": "بستر",
        "name": "زنجیرهٔ HMAC-SHA256 روی ردپای حسابرسی",
        "metric": "راست‌آزمایی",
        "value": "GET /api/audit/verify",
        "detail": "ویرایش ردیف، زنجیره را نامعتبر می‌کند",
        "source": "backend/app/core/audit.py",
    })
    return rows


def _tests() -> dict:
    path = os.path.join(VALIDATION_DIR, "coverage_snapshot.json")
    if not os.path.isfile(path):
        return {"available": False,
                "note": "پوشش هنوز ساخته نشده. tools/refresh_coverage.py را اجرا کنید."}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["available"] = True
    return data


def build_bundle() -> dict:
    ops = build_operations()
    return {
        "version": PRODUCT_VERSION,
        "as_of": ops["as_of"],
        "seed": ops["seed"],
        "note": ("حجم بازرسی، ناوگان، پر کردن و تقاضای ناحیه‌ای با بذر ثابت مصنوعی است و با موتورهای "
                 "واقعی محصول طبقه‌بندی یا پیش‌بینی شده. دقت‌های تب تکنولوژی از فایل‌های اعتبارسنجی است "
                 "و از این دادهٔ مصنوعی حساب نشده."),
        "operations": ops,
        "technologies": _technologies(),
        "tests": _tests(),
    }


def get_bundle() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = build_bundle()
    return _CACHE
