"""Unit tests of the forecasting engine: policy, models, artefacts and walk-forward helpers."""
import numpy as np
import pytest

from app.services.forecast import (ENSEMBLE, ForecastModel, KINDS, block_bootstrap_skill, design,
                                   infer_period, metrics, recommended_kind, walk_forward)


def _series(n=240, period=12, seed=0):
    rng = np.random.default_rng(seed)
    return 1000 + 120 * np.sin(np.arange(n) * 2 * np.pi / period) + rng.normal(0, 8, n)


def test_recommended_kind_policy_in_seasons():
    assert recommended_kind(5, 12)[0] == "naive"
    assert recommended_kind(13, 12)[0] == "naive"
    assert recommended_kind(14, 12)[0] == ENSEMBLE
    assert recommended_kind(131, 12)[0] == ENSEMBLE             # 10.9 seasons
    assert recommended_kind(132, 12)[0] == "ridge"              # exactly 11 seasons
    assert recommended_kind(100, 52)[0] == ENSEMBLE             # 1.9 seasons
    assert recommended_kind(599, 52)[0] == "ridge"              # 11.5 seasons
    assert all(isinstance(recommended_kind(n, 12)[1], str) and recommended_kind(n, 12)[1] for n in (5, 30, 400))


def test_infer_period():
    from datetime import date, timedelta
    d0 = date(2020, 1, 1)
    daily = [d0 + timedelta(days=i) for i in range(10)]
    weekly = [d0 + timedelta(days=7 * i) for i in range(10)]
    monthly = [d0.replace(month=(i % 12) + 1) for i in range(10)]
    assert infer_period(daily) == 7 and infer_period(weekly) == 52 and infer_period(monthly) == 12
    assert infer_period(daily[:2]) == 7


@pytest.mark.parametrize("kind", KINDS)
def test_models_fit_and_predict(kind):
    y = _series(200, 12)
    m = ForecastModel(kind, 12).fit(y)
    p = m.predict_next(y)
    assert np.isfinite(p) and p > 0
    path = m.predict_path(y, 5)
    assert len(path) == 5 and all(np.isfinite(path))
    if kind in ("ridge", "gbm", ENSEMBLE):
        assert len(m.resid_) > 0
        lo, hi = m.interval(0.8)
        assert lo < hi
    if kind == ENSEMBLE:
        assert [x.kind for x in m.members_] == ["seasonal_naive", "seasonal_drift", "ridge"]


@pytest.mark.parametrize("kind", ("ridge", "gbm", ENSEMBLE))
def test_artefact_round_trip(kind):
    y = _series(200, 12)
    m = ForecastModel(kind, 12).fit(y)
    m2 = ForecastModel.from_dict(m.to_dict())
    assert abs(m2.predict_next(y) - m.predict_next(y)) < 1e-6
    assert abs(m2.interval(0.8)[1] - m.interval(0.8)[1]) < 1e-9


def test_feature_layout_edge_cases():
    y = _series(40, 12)
    X, t, i0 = design(y, 12)
    assert X.shape[0] == t.shape[0] and i0 >= 1
    y_short = _series(10, 12)
    X2, t2, i02 = design(y_short, 12)
    assert X2.shape[0] == t2.shape[0] and i02 == 8              # n <= 2*period + lags -> i0 = n - 2


def test_metrics_and_skill():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    assert metrics(y, y)["mae"] == 0.0
    m = metrics(y, y + 1.0)
    assert m["mae"] == 1.0 and m["rmse"] == 1.0
    a = _series(41, 12, 1)
    actual, pred = a[1:], a[:-1]                               # a perfect one-step naive forecast
    sk = block_bootstrap_skill(actual, pred, pred, n_boot=50)
    assert abs(sk["skill"]) < 1e-9                             # identical models -> zero improvement


def test_walk_forward_shapes_and_bounds():
    y = _series(120, 12)
    paths, origins, lo, hi = walk_forward(y, "ridge", 12, 20, 2)
    assert paths.shape == (20, 2) and len(origins) == 20
    assert (lo <= hi).all() and origins[-1] == len(y) - 1


def test_interval_widens_with_horizon():
    y = _series(200, 12)
    m = ForecastModel("ridge", 12).fit(y)
    lo1, hi1 = m.interval(0.8, 1)
    lo4, hi4 = m.interval(0.8, 4)
    assert abs((hi4 - lo4) - 2 * (hi1 - lo1)) < 1e-6    # sqrt(4) = 2; horizon 1 is unchanged


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        ForecastModel("transformer", 12)
