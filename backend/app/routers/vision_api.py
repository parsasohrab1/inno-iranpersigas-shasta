"""FR-02 assistant: image upload -> verified model -> pass/review/reject.

D-10: the body is read in chunks and rejected as soon as it passes the byte cap.
D-16: every verdict is stored so later calibration can use operational photos.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from ..core.config import ML_DIR
from ..core.db import connect, utcnow
from ..core.security import require_role
from ..services import vision

router = APIRouter(prefix="/api/vision", tags=["vision"])
_model: vision.VisionModel | None = None
_load_error: str | None = None
MAX_IMAGE_BYTES = 15 << 20


def get_model() -> vision.VisionModel | None:
    global _model, _load_error
    if _model is None and _load_error is None:
        _model = vision.load_model(ML_DIR)
        if _model is None:
            _load_error = "model artefact backend/ml/vision_model.json is missing or unreadable"
    return _model


async def _read_capped(file: UploadFile, request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES + (1 << 20):
        raise HTTPException(413, "image larger than 15 MB")
    chunks: list[bytes] = []
    total = 0
    while True:
        block = await file.read(1 << 20)
        if not block:
            break
        total += len(block)
        if total > MAX_IMAGE_BYTES:
            raise HTTPException(413, "image larger than 15 MB")
        chunks.append(block)
    return b"".join(chunks)


def _store_verdict(user: dict, filename: str | None, digest: str, out: dict, feature_version: str) -> int:
    """Own connection: this endpoint is async, and a request-scoped connection would cross threads."""
    con = connect()
    try:
        cur = con.execute(
            "INSERT INTO vision_verdicts (created_at, actor, filename, sha256, verdict, severity, class_name, "
            "confidence, probabilities_json, model_version, feature_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), user["username"], filename, digest, out["verdict"], out["severity"], out["class"],
             out["confidence"], json.dumps(out["probabilities"]), "vision-logreg", feature_version))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


@router.post("/inspect")
async def inspect(file: UploadFile, request: Request,
                  user: dict = Depends(require_role("admin", "inspector", "quality"))):
    """Classify one cylinder photo and map the result to the FR-02 verdict rule."""
    m = get_model()
    if m is None:
        raise HTTPException(503, _load_error or "vision model not available")
    data = await _read_capped(file, request)
    try:
        feats = vision.features_from_bytes(data)
        out = m.classify(feats)
    except ValueError as e:
        text = str(e)
        raise HTTPException(413 if "pixel limit" in text else 422, text) from None
    digest = hashlib.sha256(data).hexdigest()
    out["id"] = _store_verdict(user, file.filename, digest, out, m.feature_version)
    out["image"] = {"filename": file.filename, "bytes": len(data), "sha256": digest}
    out["model"] = {"kind": "vision-logreg", "feature_version": m.feature_version,
                    "trained_on": m.meta.get("n_train"), "test_accuracy": m.meta.get("test_accuracy"),
                    "label_limitations": m.meta.get("limitations")}
    out["assistant_limit"] = ("TRL 4 assistant: labels are model-made and critical classes are rare. "
                              "A human inspector confirms every verdict; this is not a certified inspection.")
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
