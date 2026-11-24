"""FR-02 assistant: image upload -> verified model -> pass/review/reject."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ..core.config import ML_DIR
from ..core.security import require_role
from ..services import vision

router = APIRouter(prefix="/api/vision", tags=["vision"])
_model: vision.VisionModel | None = None
_load_error: str | None = None


def get_model() -> vision.VisionModel | None:
    global _model, _load_error
    if _model is None and _load_error is None:
        _model = vision.load_model(ML_DIR)
        if _model is None:
            _load_error = "model artefact backend/ml/vision_model.json is missing or unreadable"
    return _model


@router.post("/inspect")
async def inspect(file: UploadFile, user: dict = Depends(require_role("admin", "inspector", "quality"))):
    """Classify one cylinder photo and map the result to the FR-02 verdict rule."""
    m = get_model()
    if m is None:
        raise HTTPException(503, _load_error or "vision model not available")
    data = await file.read()
    if len(data) > 15 << 20:
        raise HTTPException(413, "image larger than 15 MB")
    try:
        feats = vision.features_from_bytes(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    out = m.classify(feats)
    out["image"] = {"filename": file.filename, "bytes": len(data)}
    out["model"] = {"kind": "vision-logreg", "feature_version": m.feature_version,
                    "trained_on": m.meta.get("n_train"), "test_accuracy": m.meta.get("test_accuracy"),
                    "label_limitations": m.meta.get("limitations")}
    return out


@router.get("/model")
def model_info(user: dict = Depends(require_role("admin", "inspector", "quality", "auditor"))):
    m = get_model()
    if m is None:
        raise HTTPException(503, _load_error or "vision model not available")
    meta = dict(m.meta)
    meta["classes"] = m.classes
    meta["thresholds"] = m.thresholds
    meta["feature_version"] = m.feature_version
    return meta
