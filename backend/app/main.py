"""LCIS API entry point: create_app() assembles the database, the audit middleware and all routers."""
from __future__ import annotations

from contextlib import asynccontextmanager

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .core.config import PRODUCT_ROOT, PRODUCT_VERSION
from .core.db import init_db
from .routers import audit_api, auth, cylinders, evidence, fillings, forecast, inspections, seed, vision_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    vision_api.get_model()          # preload the vision artefact (missing artefact -> 503, not a crash)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LCIS API", version=PRODUCT_VERSION, lifespan=lifespan,
                  description="LPG Cylinder Intelligent System - rule engines, forecasting, vision, evidence")
    app.add_middleware(audit_api.AuditMiddleware)
    for r in (auth.router, cylinders.router, inspections.router, fillings.router, forecast.router,
              vision_api.router, evidence.router, evidence.audit_router, seed.router):
        app.include_router(r)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "version": PRODUCT_VERSION}

    @app.get("/", tags=["meta"])
    def root():
        return {"service": "LCIS", "docs": "/docs", "health": "/health", "dashboard": "/dashboard"}

    @app.get("/dashboard", tags=["meta"], include_in_schema=False)
    def dashboard():
        path = os.path.join(PRODUCT_ROOT, "docs", "dashboard.html")
        if not os.path.isfile(path):
            raise HTTPException(404, "dashboard.html is missing")
        return FileResponse(path, media_type="text/html; charset=utf-8")
    return app


app = create_app()
