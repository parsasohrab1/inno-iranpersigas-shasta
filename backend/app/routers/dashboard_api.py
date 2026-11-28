"""Public dashboard bundle: synthetic operations, measured technologies, test coverage."""
from __future__ import annotations

from fastapi import APIRouter

from ..services.dashboard_data import get_bundle

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/bundle")
def bundle():
    return get_bundle()
