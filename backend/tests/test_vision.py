"""FR-02 vision assistant endpoint: verdict mapping with a small synthetic model artefact."""
import io
import json

import numpy as np
import pytest
from PIL import Image

from app.routers import vision_api
from app.services.vision import CLASSES, DEFAULT_THRESHOLDS, VisionModel


def _png_bytes(seed=0, size=(64, 64)):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, size=size + (3,), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def fake_vision(monkeypatch, tmp_path):
    """A tiny but real logistic-regression model on separable synthetic features (real feature dimension)."""
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    dim = vision_api.vision.features_from_bytes(_png_bytes(1)).shape[0]
    centers = np.stack([rng.normal(3.0 * i, 1.0, dim) for i in range(len(CLASSES))])
    X = np.vstack([rng.normal(c, 1.2, (30, dim)) for c in centers])
    y = sum([[c] * 30 for c in CLASSES], [])
    clf = LogisticRegression(max_iter=1000).fit(X, y)
    model = VisionModel(classes=list(CLASSES), scaler_mean=X.mean(axis=0), scaler_scale=X.std(axis=0) + 1e-9,
                        coef=clf.coef_, intercept=clf.intercept_, thresholds=dict(DEFAULT_THRESHOLDS),
                        meta={"feature_version": "test", "n_train": 180, "test_accuracy": 1.0,
                              "limitations": "synthetic test artefact"})
    path = tmp_path / "vision_model.json"
    json.dump(model.to_dict(), open(path, "w"))
    monkeypatch.setattr(vision_api, "ML_DIR", str(tmp_path))
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)
    yield model
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)


def _post(client, inspector, data=None, filename="c.png"):
    return client.post("/api/vision/inspect", files={"file": (filename, data or _png_bytes(), "image/png")},
                       headers=inspector)


def test_inspect_returns_verdict(client, inspector, fake_vision):
    r = _post(client, inspector)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verdict"] in ("pass", "review", "reject")
    assert d["severity"] in ("none", "minor", "major", "critical")
    assert abs(sum(d["probabilities"].values()) - 1.0) < 1e-6
    assert d["probabilities"][d["class"]] == max(d["probabilities"].values())
    assert d["model"]["feature_version"] == "test"
    assert d["image"]["bytes"] > 0


def test_inspect_verdict_follows_rule(client, inspector, fake_vision):
    """The synthetic model classifies the seed-0 image confidently; the rule must map it consistently."""
    d = _post(client, inspector).json()
    cls, conf = d["class"], d["confidence"]
    if cls == "ok" and conf >= 0.70:
        assert d["verdict"] == "pass"
    elif cls in ("crack", "valve_damage") and conf >= 0.60:
        assert d["verdict"] == "reject" and d["severity"] == "critical"
    else:
        assert d["verdict"] == "review"


def test_inspect_invalid_image_422(client, inspector, fake_vision):
    r = client.post("/api/vision/inspect", files={"file": ("x.png", b"not an image", "image/png")},
                    headers=inspector)
    assert r.status_code == 422 and "decodable" in r.json()["detail"]


def test_inspect_requires_role(client, tokens, fake_vision):
    r = _post(client, {"Authorization": f"Bearer {tokens['distribution1']}"})
    assert r.status_code == 403


def test_model_info(client, inspector, fake_vision):
    r = client.get("/api/vision/model", headers=inspector)
    assert r.status_code == 200 and r.json()["classes"] == list(CLASSES)


def test_missing_artefact_503(client, inspector, monkeypatch, tmp_path):
    monkeypatch.setattr(vision_api, "ML_DIR", str(tmp_path))   # an empty directory: no artefact at all
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)
    r = _post(client, inspector, filename="whatever.png")
    assert r.status_code == 503
    monkeypatch.setattr(vision_api, "_model", None)
    monkeypatch.setattr(vision_api, "_load_error", None)
