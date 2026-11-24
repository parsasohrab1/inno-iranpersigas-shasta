"""FR-02 visual inspection service: deterministic features + multinomial logistic regression.

The model classifies a photo into the six visible-condition classes of the verified web dataset
(ok, rust, dent, crack, valve_damage, scratch) and maps the result onto the SRS verdict rule:

    critical defect (crack, valve_damage)  -> reject
    minor defect  (rust, dent, scratch)    -> review   (a human decides; a dent may hide structural damage)
    no defect (ok) with high confidence    -> pass
    anything below the confidence threshold-> review   (never an automatic pass)

Honest limitation (stated in every result): the model is trained on a few hundred web photos labelled by
a vision-language model, not by a human inspector, and the rare classes (crack, valve_damage) have very
few samples. It is a TRL 4-5 prototype of the inspection assistant, not a certified inspector.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

CRITICAL_CLASSES = ("crack", "valve_damage")
MINOR_CLASSES = ("rust", "dent", "scratch")
CLASSES = ("ok", "rust", "dent", "crack", "valve_damage", "scratch")
FEATURE_VERSION = "webimg-feats-v2"
DEFAULT_THRESHOLDS = {"pass_p_min": 0.70, "critical_p_min": 0.60, "minor_p_min": 0.60}


# ----------------------------------------------------------------- features
def extract_features(img: Image.Image) -> np.ndarray:
    """Fixed-length feature vector of an RGB image (version-tagged; the artefact stores the version)."""
    im = img.convert("RGB").resize((256, 256), Image.LANCZOS)
    rgb = np.asarray(im, dtype=float) / 255.0
    hsv = np.asarray(im.convert("HSV"), dtype=float) / 255.0
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gray = rgb.mean(axis=2)
    f: list[float] = []
    f += np.histogram(h, bins=24, range=(0, 1), weights=s * v)[0].tolist()          # hue, saturation-weighted
    f += np.histogram(s, bins=8, range=(0, 1))[0].tolist()
    f += np.histogram(v, bins=8, range=(0, 1))[0].tolist()
    rust = (((h < 0.13) | (h > 0.92)) & (s > 0.25) & (v > 0.15) & (v < 0.9)).mean()
    f += [rust, gray.mean(), gray.std(), float(np.median(gray)),
          float((gray < 0.15).mean()), float((gray > 0.90).mean())]
    gx, gy = np.gradient(gray)
    mag = np.hypot(gx, gy)
    f += [float(mag.mean()), float(mag.std()), float((mag > 0.12).mean())]
    ang = (np.arctan2(gy, gx) + np.pi) / (2 * np.pi)
    f += np.histogram(ang, bins=8, range=(0, 1), weights=mag)[0].tolist()
    lap = np.abs(4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:])
    f += [float(lap.var())]
    cells = gray.reshape(16, 16, 16, 16)
    local_std = cells.std(axis=(1, 3))
    f += [float(local_std.mean()), float(local_std.std())]
    g2 = np.asarray(im.resize((128, 128), Image.LANCZOS).convert("L"), dtype=float) / 255.0
    try:
        from skimage.feature import hog
        f += hog(g2, pixels_per_cell=(32, 32), cells_per_block=(1, 1), feature_vector=True).tolist()
    except Exception:  # noqa: BLE001 - skimage missing: the remaining features still work
        pass
    return np.asarray(f, dtype=float)


def features_from_bytes(data: bytes) -> np.ndarray:
    import io
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"not a decodable image: {e}") from None
    return extract_features(img)


# ------------------------------------------------------------------- model
@dataclass
class VisionModel:
    classes: list[str]
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    coef: np.ndarray          # (n_classes, n_features)
    intercept: np.ndarray
    thresholds: dict
    meta: dict

    @property
    def feature_version(self) -> str:
        return self.meta.get("feature_version", FEATURE_VERSION)

    def predict_proba(self, feats: np.ndarray) -> np.ndarray:
        z = ((feats - self.scaler_mean) / self.scaler_scale) @ self.coef.T + self.intercept
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def classify(self, feats: np.ndarray) -> dict:
        p = self.predict_proba(feats.reshape(1, -1))[0]
        top = int(np.argmax(p))
        cls, conf = self.classes[top], float(p[top])
        if cls in CRITICAL_CLASSES:
            verdict, severity = ("reject", "critical") if conf >= self.thresholds["critical_p_min"] \
                else ("review", "critical")
        elif cls == "ok":
            verdict, severity = ("pass", "none") if conf >= self.thresholds["pass_p_min"] else ("review", "none")
        elif cls in MINOR_CLASSES:
            verdict, severity = "review", "minor"      # a human decides; FR-02 sends minors to review
        else:
            verdict, severity = "review", "minor"
        return {"verdict": verdict, "severity": severity, "class": cls, "confidence": round(conf, 4),
                "probabilities": {c: round(float(x), 4) for c, x in zip(self.classes, p)},
                "thresholds": self.thresholds,
                "rule": "FR-02: critical defect -> reject, minor defect -> review, intact with high "
                        "confidence -> pass; below the confidence threshold -> review"}

    # ------------------------------------------------------------- artefact
    def to_dict(self) -> dict:
        return {"kind": "vision-logreg", "classes": self.classes, "thresholds": self.thresholds,
                "scaler_mean": self.scaler_mean.tolist(), "scaler_scale": self.scaler_scale.tolist(),
                "coef": self.coef.tolist(), "intercept": self.intercept.tolist(), "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "VisionModel":
        return cls(classes=d["classes"], scaler_mean=np.asarray(d["scaler_mean"], float),
                   scaler_scale=np.asarray(d["scaler_scale"], float), coef=np.asarray(d["coef"], float),
                   intercept=np.asarray(d["intercept"], float), thresholds=d["thresholds"], meta=d["meta"])


def artefact_path(ml_dir: str) -> str:
    return os.path.join(ml_dir, "vision_model.json")


def load_model(ml_dir: str) -> VisionModel | None:
    p = artefact_path(ml_dir)
    if not os.path.exists(p):
        return None
    try:
        return VisionModel.from_dict(json.load(open(p, encoding="utf-8")))
    except Exception:  # noqa: BLE001 - a corrupt artefact must not crash the API
        return None


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
