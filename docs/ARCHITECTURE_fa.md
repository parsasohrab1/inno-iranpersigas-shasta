# معماری سامانه LCIS (TRL 5)

## نمای کلی

```
data/ (EIA, NIST, labels) ──► pipelines/ (build, validate, backtest, train)
                                   │
                                   ▼
                    backend/app/services/ (قواعد، پیش‌بینی، بینایی)
                                   │
                                   ▼
                    backend/app/routers/ (FastAPI: auth, cylinders, inspections,
                                          fillings, forecast, vision, evidence, audit, seed)
                                   │
                                   ▼
                    SQLite (audit_log, cylinders, inspections, fillings, demand,
                            rnd_activities, users) + artefact ها در backend/ml/
```

- **لایهٔ داده:** CSVهای واقعی EIA/NIST با مانیفست هش (`data/sources.json`)؛ تصاویر وب فقط به‌صورت
  برچسب و مانیفست (خود تصاویر به‌دلیل نامشخص بودن مجوز بازنشر نمی‌شوند).
- **لایهٔ منطق:** سرویس‌های خالص و بدون وابستگی به وب — قابل تست و قابل فراخوانی از پایپ‌لاین‌ها و API
  به‌طور یکسان (NFR-05: تعویض مدل بدون تغییر API، چون API فقط قرارداد سرویس را می‌بیند).
- **لایهٔ API:** FastAPI با احراز هویت JWT (HS256)، نقش‌های شش‌گانهٔ SRS، میان‌افزار حسابرسی که هر
  درخواست را با بازیگر، مسیر، کد وضعیت و مدت ثبت می‌کند (FR-10).
- **artefact ها:** `backend/ml/forecast_model.json` (مدل پیش‌بینی انتخاب‌شده) و
  `backend/ml/vision_model.json` (وزن‌ها + آستانه‌ها + ابرداده)؛ هر دو با هش دادهٔ آموزش.

## قرارداد API (مطابق SRS)

| نقطهٔ پایانی | FR | نقش‌های مجاز |
|---|---|---|
| GET /health | - | عمومی |
| POST /api/auth/token، GET /api/auth/me | FR-09 | عمومی/احرازشده |
| POST/GET /api/cylinders، GET /api/cylinders/{id} | FR-01 | admin، inspector برای ایجاد |
| GET /api/cylinders/{id}/lifecycle | FR-04 | احرازشده |
| POST/GET /api/inspections | FR-02، FR-03 | admin، inspector، quality |
| POST/GET /api/fillings، GET /api/fillings/anomalies | FR-05، FR-06 | admin، inspector برای ثبت |
| GET /api/forecast | FR-07 | احرازشده |
| POST /api/vision/inspect، GET /api/vision/model | FR-02 | admin، inspector، quality |
| GET /api/evidence/rnd، POST /api/evidence/rnd/activities | FR-08 | admin، auditor، quality |
| GET /api/audit، GET /api/audit/summary | FR-10 | admin، auditor |
| POST /api/seed | - | admin |

## پایگاه داده

SQLite (کتابخانهٔ استاندارد، بدون ORM) با WAL؛ طرح جداول همان مدل دادهٔ SRS است به‌علاوهٔ
`users`، `rnd_activities` و `audit_log`. مسیر پایگاه داده با `LCIS_DB_PATH` قابل تنظیم است
(در Docker: `/var/lib/lcis/lcis.db` با volume).

## استقرار و امنیت

- Dockerfile مبتنی بر python:3.12-slim؛ TLS در NFR-03 توسط reverse proxy خاتمه می‌یابد و سند استقرار
  همین را تصریح می‌کند.
- رمز عبورها با pbkdf2-hmac (۱۲۰٬۰۰۰ دور) و نمک تصادفی؛ توکن JWT با انقضا؛ رمز امضا از متغیر محیطی
  `LCIS_SECRET` (در تولید الزامی؛ مقدار پیش‌فرض فقط برای توسعه).
- کنترل دسترسی نقش‌محور با `require_role` در همهٔ نقاط پایان نوشتن/خواندن حساس.

## آزمون و CI

`backend/tests/` (۸۷ آزمون، پوشش ۹۵٪)؛ GitHub Actions هر push را با `--cov-fail-under=85` می‌آزماید.
`run_validation.py` در سطح محصول همهٔ مجموعه‌های اعتبارسنجی را یک‌جا اجرا و خلاصه می‌سازد.
