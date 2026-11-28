"""Synthetic operations are seeded, and the dashboard bundle exposes them with measured metrics."""


def test_operations_are_deterministic_and_classified():
    from app.services.synthetic_ops import build_operations
    a = build_operations()
    b = build_operations()
    assert a == b
    assert a["synthetic"] is True and a["seed"] == 20261128
    ins = a["inspection"]
    assert sum(row["n"] for row in ins["by_verdict"]) == ins["n"] == 7 * 80
    assert {row["code"] for row in ins["by_verdict"]} <= {"pass", "review", "reject"}
    assert a["lifecycle"]["n"] == 360
    assert a["filling"]["n"] == 6 * 60
    assert all(len(r["forecast"]) == 7 for r in a["distribution"]["regions"])


def test_bundle_keeps_measured_accuracy_apart_from_synthetic_volumes(client):
    r = client.get("/api/dashboard/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["operations"]["synthetic"] is True
    names = [row["name"] for row in body["technologies"]]
    assert any("ریج" in name for name in names)
    ridge = next(row for row in body["technologies"] if "ریج با جمله" in row["name"])
    assert ridge["value"].startswith("262.")
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "تکنولوژی‌های به‌کاررفته" in page.text and "آزمون‌ها و پوشش" in page.text
