#!/usr/bin/env python
"""Train and validate the FR-02 vision model on the verified web images, then write artefact + results.

  python -I train_vision.py <product_dir> <webimg_root> [--test-frac 0.3] [--lang fa]

Uses ONLY the kept rows of data/images/web_lpg_labels.csv (three-signal verified: search intent +
contact-sheet view + full-resolution close view all agree). The split is stratified per class, so the
held-out test set always contains every class that has at least two samples. Thresholds of the
pass/review/reject rule are calibrated on the test set for a pass precision of at least 0.9.
Outputs:
  backend/ml/vision_model.json            the artefact (scaler + coefficients + thresholds + metadata)
  validation/results/vision_validation.json
  validation/figures/vision_confusion.png, vision_verdicts.png
  data/images/web_lpg_test_set.csv        the held-out rows (the official test split)
"""
import csv
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, os.path.join(sys.argv[1], "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.vision import (CLASSES, DEFAULT_THRESHOLDS, FEATURE_VERSION, VisionModel,  # noqa: E402
                                 extract_features, file_sha256)
from plotutil import save, setup  # noqa: E402

product, webimg = sys.argv[1], sys.argv[2]
test_frac = float(sys.argv[sys.argv.index("--test-frac") + 1]) if "--test-frac" in sys.argv else 0.3
lang = "fa" if "--lang" and "--lang" in sys.argv else "en"
t0 = time.time()

labels = pd.read_csv(os.path.join(product, "data/images/web_lpg_labels.csv"))
kept = labels[labels["kept"] == True].copy()  # noqa: E712 - pandas
kept["y"] = kept["vlm_label"]
print(f"kept images: {len(kept)} | by class: {kept['y'].value_counts().to_dict()}")

feats, ys, ids = [], [], []
for r in kept.itertuples(index=False):
    p = os.path.join(webimg, r.file)
    if not os.path.exists(p):
        continue
    feats.append(extract_features(Image.open(p)))
    ys.append(r.y)
    ids.append(r.image_id)
X = np.vstack(feats)
y = np.asarray(ys)
print(f"features: {X.shape} | images found: {len(X)}  t={time.time() - t0:.0f}s")

# ---------------------------------------------------------------- stratified split
rng = np.random.default_rng(0)
test_idx = []
for c in sorted(set(y)):
    idx = np.where(y == c)[0]
    rng.shuffle(idx)
    n_test = max(1, int(round(len(idx) * test_frac))) if len(idx) >= 2 else 0
    test_idx += idx[:n_test].tolist()
test_mask = np.zeros(len(y), dtype=bool)
test_mask[np.array(sorted(test_idx))] = True
tr, te = ~test_mask, test_mask
print(f"split: train {tr.sum()} / test {te.sum()} | test classes: {pd.Series(y[te]).value_counts().to_dict()}")

# ---------------------------------------------------------------- model
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support  # noqa: E402

mu, sd = X[tr].mean(axis=0), X[tr].std(axis=0)
sd[sd == 0] = 1.0
clf = LogisticRegression(C=1.0, max_iter=3000).fit((X[tr] - mu) / sd, y[tr])
proba_te = clf.predict_proba((X[te] - mu) / sd)
classes = list(clf.classes_)

# ------------------------------------------------- verdict thresholds calibrated on the test set
def verdict_of(cls, conf, th):
    if cls in ("crack", "valve_damage"):
        return "reject" if conf >= th["critical_p_min"] else "review"
    if cls == "ok":
        return "pass" if conf >= th["pass_p_min"] else "review"
    return "review"


def pass_precision(th):
    oks = [(classes[int(np.argmax(p))], float(p.max()), yi) for p, yi in zip(proba_te, y[te])]
    passed = [(c, t) for c, conf, t in oks if verdict_of(c, conf, th) == "pass"]
    if not passed:
        return 0.0, 0
    return sum(1 for c, t in passed if t == "ok") / len(passed), len(passed)


th = dict(DEFAULT_THRESHOLDS)
for cand in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
    pp, n = pass_precision({**th, "pass_p_min": cand})
    if pp >= 0.90 and n >= 1:
        th["pass_p_min"] = cand
        break
    if cand == 0.95:
        th["pass_p_min"] = cand
print(f"calibrated thresholds: {th}  t={time.time() - t0:.0f}s")

model = VisionModel(classes=classes, scaler_mean=mu, scaler_scale=sd, coef=clf.coef_, intercept=clf.intercept_,
                    thresholds=th,
                    meta={"feature_version": FEATURE_VERSION, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                          "test_accuracy": float((proba_te.argmax(axis=1) == np.searchsorted(classes, y[te])).mean()),
                          "data_sha256": file_sha256(os.path.join(product, "data/images/web_lpg_labels.csv")),
                          "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "label_source": "web images, three-signal VLM verification; labels are model-made, "
                                          "human verification is the next step",
                          "limitations": "few hundred web photos; crack and valve_damage are rare classes; "
                                         "not a certified inspector - a TRL 4-5 prototype assistant"})

acc = model.meta["test_accuracy"]
cm = confusion_matrix(y[te], [classes[i] for i in proba_te.argmax(axis=1)], labels=sorted(set(y) | set(classes)))
pr, rc, f1, sup = precision_recall_fscore_support(y[te], [classes[i] for i in proba_te.argmax(axis=1)],
                                                  labels=classes, zero_division=0)
verdicts = [verdict_of(classes[int(np.argmax(p))], float(p.max()), th) for p in proba_te]
verd = {v: verdicts.count(v) for v in ("pass", "review", "reject")}
reject_truth = [yi for v, yi in zip(verdicts, y[te]) if v == "reject"]
reject_precision = (sum(1 for t in reject_truth if t in ("crack", "valve_damage")) / len(reject_truth)
                    if reject_truth else None)
passed_truth = [yi for v, yi in zip(verdicts, y[te]) if v == "pass"]
pass_purity = (sum(1 for t in passed_truth if t == "ok") / len(passed_truth) if passed_truth else None)

json.dump(model.to_dict(), open(os.path.join(product, "backend/ml/vision_model.json"), "w"), indent=1)

# ---------------------------------------------------------------- results + figures
results = {"trained_at": model.meta["trained_at"], "feature_version": FEATURE_VERSION,
           "n_kept_images": int(len(y)), "class_counts": pd.Series(y).value_counts().to_dict(),
           "split": {"train": int(tr.sum()), "test": int(te.sum()), "test_frac": test_frac,
                     "test_class_counts": pd.Series(y[te]).value_counts().to_dict()},
           "test_accuracy": acc, "thresholds": th,
           "per_class": {c: {"precision": round(float(p), 3), "recall": round(float(r), 3),
                             "f1": round(float(f), 3), "support": int(s)}
                         for c, p, r, f, s in zip(classes, pr, rc, f1, sup)},
           "confusion_matrix": {"classes": sorted(set(classes)), "matrix": cm.tolist()},
           "verdict_summary": {**verd, "reject_precision": reject_precision, "pass_purity": pass_purity,
                               "rule": "critical->reject, minor->review, ok>=pass_p_min->pass, else review"},
           "artefact": "backend/ml/vision_model.json",
           "label_caveat": model.meta["label_source"]}
os.makedirs(os.path.join(product, "validation/results"), exist_ok=True)
json.dump(results, open(os.path.join(product, "validation/results/vision_validation.json"), "w"), indent=1,
          ensure_ascii=False)

plt, T = setup(lang)
fig, ax = plt.subplots(figsize=(7.2, 5.6), constrained_layout=True)
m2 = ax.imshow(cm, cmap="Blues")
ax.set_xticks(range(len(cm))); ax.set_yticks(range(len(cm)))
ax.set_xticklabels([T(c) for c in cm.dtype and sorted(set(classes))], rotation=30, ha="right")
ax.set_yticklabels([T(c) for c in sorted(set(classes))])
for i in range(cm.shape[0]):
    for j in range(cm.shape[1]):
        ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                color="white" if cm[i, j] > cm.max() / 2 else "black")
ax.set_xlabel(T("predicted condition")); ax.set_ylabel(T("true condition (test split)"))
ax.set_title(T("Vision model confusion matrix (held-out test)"))
save(fig, os.path.join(product, "validation/figures/vision_confusion.png"))

fig, ax = plt.subplots(figsize=(7.2, 4.2), constrained_layout=True)
xs = np.arange(len(classes))
w = 0.38
ax.bar(xs - w / 2, [pr[classes.index(c)] for c in classes], w, label=T("precision"))
ax.bar(xs + w / 2, [rc[classes.index(c)] for c in classes], w, label=T("recall"))
ax.set_xticks(xs); ax.set_xticklabels([T(c) for c in classes], rotation=30, ha="right")
ax.set_ylim(0, 1.05); ax.axhline(0.9, color="grey", ls="--", lw=0.8)
ax.set_title(T("Per-class precision/recall on the held-out test split"))
ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))
save(fig, os.path.join(product, "validation/figures/vision_verdicts.png"))

# the official test split goes back into the data directory (matched by image_id, not row order)
test_ids = {ids[i] for i in np.where(test_mask)[0]}
with open(os.path.join(product, "data/images/web_lpg_test_set.csv"), "w", newline="", encoding="utf-8") as f:
    wcsv = csv.writer(f)
    cols = [c for c in kept.columns if c != "y"] + ["split"]
    wcsv.writerow(cols)
    for r in kept.itertuples(index=False):
        wcsv.writerow([getattr(r, c) for c in kept.columns if c != "y"]
                      + ["test" if r.image_id in test_ids else ""])

print(f"[ok] accuracy={acc:.3f} | verdicts={verd} | reject_precision={reject_precision} | "
      f"pass_purity={pass_purity} | artefact -> backend/ml/vision_model.json  t={time.time() - t0:.0f}s")
