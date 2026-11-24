"""Unit tests of the filling/lot rule engine (FR-03, FR-05) with traceable regulatory thresholds."""
import pytest

from app.services.filling import (MAV_EXAMPLE, RETURN_BANDS, assess_filling, assess_lot, assess_tare,
                                  capacity_lb, overfill_severity, return_band, tare_band)


def test_tare_tiers():
    assert assess_tare(30.0, 30.09)["tier"] == "hb130"          # +0.3 % inside +/- 0.5 %
    assert assess_tare(30.0, 29.25)["tier"] == "dot"            # -2.5 % inside DOT [-3, 1]
    assert assess_tare(30.0, 28.8)["tier"] == "noncompliant"    # -4 % outside the DOT band
    out = assess_tare(30.0, 28.2)                               # -6 % -> requalification required
    assert out["tier"] == "requalify" and out["action"] == "reject"
    assert "180.209(c)" in out["source"]
    assert assess_tare(30.0, 30.0)["severity"] == "none"


def test_tare_band_by_capacity():
    assert tare_band(None) == (-3.0, 1.0)
    assert tare_band(20.0) == (-3.0, 1.0)
    assert tare_band(100.0) == (-2.0, 1.0)
    assert tare_band(capacity_lb(45.0, "kg")) == (-2.0, 1.0)    # 45 kg ~ 99 lb -> over-25 lb band


def test_overfill_bands():
    assert overfill_severity(0.0, 100.0) == "up_to_5_pct"
    assert overfill_severity(4.9, 100.0) == "up_to_5_pct"
    assert overfill_severity(6.0, 100.0) == "5_to_10_pct"
    assert overfill_severity(12.0, 100.0) == "10_to_15_pct"
    assert overfill_severity(17.0, 100.0) == "15_to_20_pct"
    assert overfill_severity(25.0, 100.0) == "over_20_pct"


def test_filling_ok_within_mav():
    out = assess_filling(15.0, 14.95)
    assert out["anomaly"] == "ok" and out["action"] == "accept"
    assert "below label" in out["detail"]                       # inside the default MAV (15 lb -> 0.28 lb)


def test_filling_short_fill_uses_mav():
    out = assess_filling(15.0, 14.5)                            # 0.5 lb short > 0.28 lb MAV
    assert out["anomaly"] == "short_fill" and out["action"] == "rework"
    assert "MAV" in out["detail"]


def test_filling_short_fill_exact_mav_passes():
    mav = 15.0 * MAV_EXAMPLE[1] / MAV_EXAMPLE[0]                # exactly at the MAV boundary -> acceptable
    assert assess_filling(15.0, 15.0 - mav)["anomaly"] == "ok"


def test_filling_overfill_with_capacity():
    out = assess_filling(15.0, 15.6, rated_capacity=15.0)
    assert out["anomaly"] == "overfill" and out["action"] == "reject"
    assert out["overfill_severity"] == "up_to_5_pct"


def test_filling_overfill_legacy_heuristic_without_capacity():
    out = assess_filling(10.0, 12.5)                            # +25 % with no rated capacity
    assert out["anomaly"] == "overfill" and out["action"] == "review"
    assert "first-SRS heuristic" in out["detail"]


def test_filling_leak_first():
    out = assess_filling(15.0, 10.0, leak_signal=True)
    assert out["anomaly"] == "leak" and out["severity"] == "critical"
    assert out["checks"][-1]["check"] == "leak"


def test_filling_tare_loss_beats_short_fill():
    out = assess_filling(15.0, 10.0, tare_marked=30.0, tare_measured=28.2)   # -6 % tare loss
    assert out["anomaly"] == "tare_loss" and out["action"] == "reject"


def test_filling_tare_noncompliant_review():
    out = assess_filling(15.0, 15.0, tare_marked=30.0, tare_measured=28.8)   # -4 %
    assert out["anomaly"] == "tare_out_of_tolerance" and out["action"] == "review"


def test_filling_scale_tolerance_widens_acceptance():
    assert assess_filling(15.0, 14.6, tare_marked=None,
                          tare_measured=None)["anomaly"] == "short_fill"
    from app.services.filling import FillPolicy
    out = assess_filling(15.0, 14.6, policy=FillPolicy(scale_tolerance=0.2))
    assert out["anomaly"] == "ok"


def test_filling_input_validation():
    with pytest.raises(ValueError):
        assess_filling(0.0, 1.0)
    with pytest.raises(ValueError):
        assess_filling(15.0, -0.1)


def test_lot_acceptance_categories():
    ok = assess_lot([0.1, 0.0, 0.2, -0.1, 0.05, 0.0, 0.1, 0.15, -0.05, 0.0, 0.1, 0.05], mav=0.28)
    assert ok["passes"] and ok["category"] == "pass"
    heavy_spread = assess_lot([-0.2, 0.3, -0.1, 0.25, 0.0, -0.05, 0.2, 0.1, -0.15, 0.05, 0.15, 0.1], mav=0.28)
    assert heavy_spread["passes"] or heavy_spread["category"] in ("average_only", "mav_only")
    with pytest.raises(ValueError):
        assess_lot([0.1], mav=0.28)
    with pytest.raises(ValueError):
        assess_lot([0.1, 0.2], mav=0.28)                        # n=2 has no sampling plan


def test_return_bands():
    assert return_band(-0.5) == RETURN_BANDS[0]
    assert return_band(0.7) == RETURN_BANDS[1]
    assert return_band(2.0) == RETURN_BANDS[2]
    assert return_band(4.0) == RETURN_BANDS[3]
    assert return_band(6.0) == RETURN_BANDS[4]
