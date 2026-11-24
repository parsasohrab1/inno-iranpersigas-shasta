"""SQLite storage (stdlib sqlite3 - no ORM dependency; the schema matches the SRS data model).

One connection per request via `get_db`; WAL mode so reads never block the writer. The schema is created
idempotently on startup. All writes go through helpers that set `created_at`/`actor` so FR-10 holds.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cylinders (
    id INTEGER PRIMARY KEY, serial TEXT UNIQUE NOT NULL, barcode TEXT UNIQUE, manufacture_date TEXT NOT NULL,
    last_hydro_test TEXT, status TEXT NOT NULL DEFAULT 'in_service', region TEXT, fill_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0, last_inspection_date TEXT, last_inspection_result TEXT,
    last_inspection_severity TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY, cylinder_id INTEGER NOT NULL REFERENCES cylinders(id), created_at TEXT NOT NULL,
    defects_json TEXT NOT NULL, result TEXT NOT NULL, severity TEXT NOT NULL, image_url TEXT, actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fillings (
    id INTEGER PRIMARY KEY, cylinder_id INTEGER NOT NULL REFERENCES cylinders(id), station_id TEXT NOT NULL,
    created_at TEXT NOT NULL, target_weight REAL NOT NULL, actual_weight REAL NOT NULL, leak_signal INTEGER NOT NULL,
    anomaly TEXT NOT NULL, action TEXT NOT NULL, detail TEXT, actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demand (
    id INTEGER PRIMARY KEY, region TEXT NOT NULL, day TEXT NOT NULL, demand REAL NOT NULL,
    UNIQUE(region, day));
CREATE TABLE IF NOT EXISTS rnd_activities (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL, hours REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0, note TEXT, actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT, method TEXT NOT NULL, path TEXT NOT NULL,
    status_code INTEGER NOT NULL, duration_ms REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_insp_cyl ON inspections(cylinder_id);
CREATE INDEX IF NOT EXISTS idx_fill_cyl ON fillings(cylinder_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(path: str | None = None) -> None:
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def get_db():
    """FastAPI dependency: one connection per request, always closed."""
    con = connect()
    try:
        yield con
    finally:
        con.close()
