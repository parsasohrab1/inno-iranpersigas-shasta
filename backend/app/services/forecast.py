"""Demand forecasting services (FR-07).

Every model is a small, self-contained forecaster with the same interface:

    m = ForecastModel(kind="ridge", period=52).fit(history)      # history: sequence of past values
    m.predict_next(history)      -> float        # one step (one week for weekly data) ahead
    m.predict_path(history, 7)   -> list[float]   # recursive multi-step path
    m.interval(0.8)              -> (lo, hi)      # central prediction interval from held-out residuals

Kinds
    naive                 last observed value
    moving_average_14d    mean of the last 14 values (the MVP baseline shipped in the first SRS)
    seasonal_naive        value one season ago
    seasonal_drift        seasonal naive plus the change of the 4-point level since last season
    ridge                 dynamic harmonic regression (Fourier terms + lags + rolling means, ridge fit)
    gbm                   gradient-boosted trees on the same features (scikit-learn HistGradientBoosting)
    ensemble_seasonal_ridge   plain mean of seasonal_naive, seasonal_drift and ridge

Method policy (recommended_kind), fixed before the final evaluation from the walk-forward evidence on the
real EIA series (validation/results/forecast_ensemble.json):
  * fewer than 14 observations            -> naive
  * 600 observations or more              -> ridge alone
  * anything in between                   -> ensemble_seasonal_ridge
On the 10 regional monthly series the combination was within 4 % of the best method on average and never
worse than the MVP moving average in more than 2 of 10 series, while ridge alone was up to 62 % worse; on the
weekly national series ridge alone pulled ahead of the combination only once about 600-800 observations
were available. Daily series could not be validated on real data (none is public), so they get the
conservative branch and the API says so.

Only data before each forecast origin is ever used (walk-forward), and every model is deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ENSEMBLE = "ensemble_seasonal_ridge"
ENSEMBLE_MEMBERS = ("seasonal_naive", "seasonal_drift", "ridge")
ENSEMBLES = {ENSEMBLE: ENSEMBLE_MEMBERS}
KINDS = ("naive", "moving_average_14d", "seasonal_naive", "seasonal_drift", "ridge", "gbm", ENSEMBLE)
LEARNED = ("ridge", "gbm")
N_LAGS = 4
N_FOURIER = 3
MIN_HISTORY = {"naive": 1, "moving_average_14d": 14, "seasonal_naive": 1, "seasonal_drift": 1, ENSEMBLE: 1}
RIDGE_MIN_HISTORY = 600      # observations needed before the learned model is used on its own
MIN_RESIDUALS = 8


def infer_period(dates) -> int:
    """Season length in samples from the median spacing of the observation dates."""
    d = sorted(dates)
    if len(d) < 3:
        return 7
    gaps = sorted((b - a).days for a, b in zip(d, d[1:]))
    g = gaps[len(gaps) // 2]
    if g <= 2:
        return 7      # daily observations -> weekly cycle
    if g <= 20:
        return 52     # weekly observations -> yearly cycle
    return 12         # monthly observations -> yearly cycle


def fourier(idx: np.ndarray, period: int, k_max: int = N_FOURIER) -> np.ndarray:
    cols = []
    for k in range(1, k_max + 1):
        cols.append(np.sin(2 * np.pi * k * idx / period))
        cols.append(np.cos(2 * np.pi * k * idx / period))
    return np.column_stack(cols)


def _layout(n: int, period: int):
    """First training index and the seasonal blocks available for a history of n values."""
    i0 = 2 * period
    if n <= i0 + N_LAGS:
        i0 = max(N_LAGS, n - 2)
    return i0, i0 >= period, i0 >= 2 * period


def feature_rows(y: np.ndarray, idx: np.ndarray, period: int, seasonal: bool, seasonal2: bool) -> np.ndarray:
    """Predictors of y[i] for every i in idx, each built from y[:i] only.

    Used for the training rows and for the single row of the next (not yet observed) point, so both are
    always built by the same code.
    """
    y = np.asarray(y, dtype=float)
    idx = np.asarray(idx, dtype=int)
    S = np.concatenate([[0.0], np.cumsum(y)])
    cols = [np.ones(len(idx))]
    for L in range(1, N_LAGS + 1):
        cols.append(y[idx - L])
    if seasonal:
        cols += [y[idx - period], (S[idx] - S[idx - period]) / period]
    if seasonal2:
        cols += [y[idx - 2 * period], (S[idx - period] - S[idx - 2 * period]) / period]
    width = np.minimum(13, idx)
    cols.append((S[idx] - S[idx - width]) / np.maximum(width, 1))
    cols.append(fourier(idx, period))
    return np.column_stack(cols)


def design(y: np.ndarray, period: int):
    """Design matrix whose row j only uses values before index i0 + j and predicts y[i0 + j]."""
    y = np.asarray(y, dtype=float)
    i0, seasonal, seasonal2 = _layout(len(y), period)
    idx = np.arange(i0, len(y))
    return feature_rows(y, idx, period, seasonal, seasonal2), y[idx], i0


def _ridge_fit(X: np.ndarray, t: np.ndarray, lam: float = 1.0):
    """Ridge regression on standardised predictors (intercept unpenalised). Returns coef, mu, sd."""
    mu = X[:, 1:].mean(axis=0)
    sd = X[:, 1:].std(axis=0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X[:, 1:] - mu) / sd])
    A = Z.T @ Z + lam * np.eye(Z.shape[1])
    A[0, 0] -= lam
    return np.linalg.solve(A, Z.T @ t), mu, sd


def _gbm():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20,
                                         l2_regularization=1.0, early_stopping=False, random_state=0)


def _baseline_next(kind: str, y: np.ndarray, period: int) -> float:
    if kind == "naive":
        return float(y[-1])
    if kind == "moving_average_14d":
        return float(y[-14:].mean())
    if kind == "seasonal_naive":
        return float(y[-period]) if len(y) >= period else float(y[-1])
    if kind == "seasonal_drift":
        if len(y) >= period + 4:
            return float(y[-period] + y[-4:].mean() - y[-period - 4:-period].mean())
        return float(y[-1])
    raise ValueError(kind)


@dataclass
class ForecastModel:
    kind: str = "ridge"
    period: int = 52
    min_history: int = field(init=False, default=0)
    coef_: np.ndarray | None = None
    mu_: np.ndarray | None = None
    sd_: np.ndarray | None = None
    tree_: object = None
    members_: list | None = None
    layout_: tuple = (True, True)          # (seasonal block, second seasonal block) used at fit time
    resid_: np.ndarray = field(default_factory=lambda: np.zeros(0))
    trained_on_: int = 0

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown forecast kind '{self.kind}' (choose from {KINDS})")
        self.min_history = MIN_HISTORY.get(self.kind, 3 * self.period)

    # ------------------------------------------------------------------ fit
    def fit(self, history) -> "ForecastModel":
        y = np.asarray(history, dtype=float)
        if len(y) < 2:
            raise ValueError("at least two observations are required")
        self.trained_on_ = len(y)
        if self.kind in ENSEMBLES:
            self.members_ = [ForecastModel(k, self.period).fit(y) for k in ENSEMBLES[self.kind]]
            n = min(len(m.resid_) for m in self.members_)            # common tail, same target points
            self.resid_ = (np.mean([m.resid_[-n:] for m in self.members_], axis=0) if n >= MIN_RESIDUALS
                           else np.zeros(0))
        elif self.kind in LEARNED:
            i0, seasonal, seasonal2 = _layout(len(y), self.period)
            self.layout_ = (seasonal, seasonal2)
            idx = np.arange(i0, len(y))
            X = feature_rows(y, idx, self.period, seasonal, seasonal2)
            t = y[idx]
            cut = int(len(t) * 0.75)                      # held-out tail for honest interval residuals
            self.resid_ = (self._fit_learned(X[:cut], t[:cut], X[cut:], t[cut:])
                           if len(t) - cut >= MIN_RESIDUALS else np.zeros(0))
            self._fit_learned(X, t, None, None)
        else:
            first = max(len(y) // 3, self.period + 4, 14)
            self.resid_ = np.array([y[i] - _baseline_next(self.kind, y[:i], self.period)
                                    for i in range(first, len(y))])
        return self

    def _fit_learned(self, X, t, Xh, th) -> np.ndarray:
        if self.kind == "ridge":
            self.coef_, self.mu_, self.sd_ = _ridge_fit(X, t) if len(t) >= 2 else (None, None, None)
            if self.coef_ is None:
                return np.zeros(0)
            pred = self._ridge_predict
        else:
            if len(t) < 2:
                self.tree_ = None
                return np.zeros(0)
            self.tree_ = _gbm().fit(X, t)
            pred = self.tree_.predict
        return (pred(Xh) - th) if Xh is not None and len(Xh) else np.zeros(0)

    def _ridge_predict(self, A: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(len(A)), (A[:, 1:] - self.mu_) / self.sd_]) @ self.coef_

    @property
    def _is_fitted(self) -> bool:
        if self.kind in ENSEMBLES:
            return bool(self.members_)
        if self.kind == "ridge":
            return self.coef_ is not None
        if self.kind == "gbm":
            return self.tree_ is not None
        return True

    # --------------------------------------------------------------- predict
    def predict_next(self, history) -> float:
        y = np.asarray(history, dtype=float)
        if len(y) == 0:
            return 0.0
        if len(y) < 2:
            return float(y[-1])
        if self.kind not in ENSEMBLES and self.kind not in LEARNED:
            return _baseline_next(self.kind, y, self.period)
        if self.kind in LEARNED and len(y) < max(self.min_history, 2):
            return float(y[-14:].mean())
        if not self._is_fitted:
            self.fit(y)
        if self.kind in ENSEMBLES:
            return float(np.mean([m.predict_next(y) for m in self.members_]))
        if not self._is_fitted:                        # too little history even after fitting
            return float(y[-14:].mean())
        seasonal, seasonal2 = self.layout_
        # the row of the NEXT point (index n), built exactly like the training rows
        row = feature_rows(y, np.array([len(y)]), self.period, seasonal, seasonal2)
        if self.kind == "ridge":
            return float(self._ridge_predict(row)[0])
        return float(self.tree_.predict(row)[0])

    def predict_path(self, history, horizon: int) -> list[float]:
        y = [float(v) for v in history]
        out = []
        for _ in range(int(horizon)):
            p = max(self.predict_next(y), 0.0)
            out.append(p)
            y.append(p)
        return out

    def interval(self, level: float = 0.8) -> tuple[float, float]:
        """Offsets (lo, hi) of the central interval around the point forecast, from held-out residuals."""
        r = np.asarray(self.resid_, dtype=float)
        if len(r) < MIN_RESIDUALS:
            return (0.0, 0.0)
        a = (1.0 - level) / 2.0
        return float(np.quantile(r, a)), float(np.quantile(r, 1.0 - a))

    # ------------------------------------------------------------- artefact
    def to_dict(self) -> dict:
        d = {"kind": self.kind, "period": self.period, "q80": list(self.interval(0.8)), "n": self.trained_on_}
        if self.kind in ENSEMBLES:
            d["members"] = [m.to_dict() for m in self.members_]
            d["resid"] = [float(v) for v in self.resid_]
        elif self.kind == "gbm":
            import pickle
            d["tree"] = pickle.dumps(self.tree_).hex()       # loaded only from artefacts written by this package
        elif self.kind == "ridge":
            d.update(coef=[float(c) for c in self.coef_], mu=[float(c) for c in self.mu_],
                     sd=[float(c) for c in self.sd_])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ForecastModel":
        m = cls(kind=d["kind"], period=int(d["period"]))
        m.trained_on_ = int(d["n"])
        m.layout_ = _layout(int(d["n"]), int(d["period"]))[1:]
        q = d.get("q80", [0.0, 0.0])
        # eight repeated values keep interval(0.8) exactly equal to the stored interval
        m.resid_ = np.array([q[0], q[0], q[1], q[1], q[0], q[1], q[0], q[1]], dtype=float)
        if d["kind"] in ENSEMBLES:
            m.members_ = [cls.from_dict(x) for x in d["members"]]
            if len(d.get("resid") or []) >= MIN_RESIDUALS:
                m.resid_ = np.asarray(d["resid"], dtype=float)
        elif d["kind"] == "gbm":
            import pickle
            m.tree_ = pickle.loads(bytes.fromhex(d["tree"]))
        elif d.get("coef") is not None:
            m.coef_ = np.asarray(d["coef"], dtype=float)
            m.mu_ = np.asarray(d["mu"], dtype=float)
            m.sd_ = np.asarray(d["sd"], dtype=float)
        return m


def recommended_kind(n_history: int) -> tuple[str, str]:
    """(kind, reason) that the walk-forward evidence supports for a history of this length."""
    if n_history < 14:
        return "naive", "fewer than 14 observations: the MVP 14-point mean is not available"
    if n_history >= RIDGE_MIN_HISTORY:
        return "ridge", (f"{n_history} observations: the learned model beat every other method on the real "
                         f"weekly series once this much history was available")
    return ENSEMBLE, (f"{n_history} observations: mean of the seasonal naive, seasonal drift and ridge "
                      f"forecasts, the most robust method on the real monthly series of this length")


# --------------------------------------------------------------------- evaluation
def metrics(actual, predicted) -> dict:
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    e = predicted - actual
    denom = np.where(actual == 0, np.nan, actual)
    return {"n": int(len(e)), "mae": float(np.mean(np.abs(e))), "rmse": float(math.sqrt(np.mean(e ** 2))),
            "mape": float(np.nanmean(np.abs(e) / np.abs(denom)) * 100),
            "wape": float(np.abs(e).sum() / max(1e-9, np.abs(actual).sum()) * 100), "bias": float(np.mean(e))}


def walk_forward(y, kind: str, period: int, n_origins: int, horizon: int = 1, refit_every: int = 1):
    """Forecasts from rolling origins: origin k uses y[:k] only.

    Returns paths (n_origins x horizon), origins, and the lower/upper 80 % bounds of the one-step forecast.
    """
    y = np.asarray(y, float)
    n = len(y)
    start = n - int(n_origins)
    model = ForecastModel(kind, period)
    refits = kind in LEARNED or kind in ENSEMBLES
    fitted, paths, lo, hi = None, [], [], []
    for k in range(start, n):
        step = k - start
        if fitted is None or (refits and step % refit_every == 0):
            fitted = model.fit(y[:k])
        paths.append(fitted.predict_path(y[:k], horizon))
        l, h = fitted.interval(0.8)
        p = fitted.predict_next(y[:k])
        lo.append(p + l)
        hi.append(p + h)
    return np.array(paths), np.arange(start, n), np.array(lo), np.array(hi)


def block_bootstrap_skill(actual, pred_a, pred_b, block: int = 8, n_boot: int = 2000, seed: int = 0):
    """MSE skill score of model a against reference b with a moving-block-bootstrap 95 % interval."""
    rng = np.random.default_rng(seed)
    actual, pa, pb = (np.asarray(v, float) for v in (actual, pred_a, pred_b))
    la, lb = (actual - pa) ** 2, (actual - pb) ** 2
    n = len(la)
    scores = []
    nblk = int(math.ceil(n / block))
    for _ in range(n_boot):
        starts = rng.integers(0, max(1, n - block), size=nblk)
        ia = np.concatenate([la[s:s + block] for s in starts])[:n]
        ib = np.concatenate([lb[s:s + block] for s in starts])[:n]
        scores.append(1 - ia.mean() / ib.mean())
    lo, hi = np.quantile(scores, [0.025, 0.975])
    return {"skill": float(1 - la.mean() / lb.mean()), "ci95": [float(lo), float(hi)]}
