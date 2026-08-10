from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_click_state_contains_lifecycle_counts_not_unitless_revenue() -> None:
    model = (ROOT / "core/models/trackers/click_state.py").read_text(encoding="utf-8")
    processor = (ROOT / "core/adset_pro/processing.py").read_text(encoding="utf-8")
    baseline = (ROOT / "migrations/versions/0001_safety_first_baseline.sql").read_text(
        encoding="utf-8"
    )

    for legacy_field in ("ftd_revenue", "redeposit_revenue"):
        assert legacy_field not in model
        assert legacy_field not in processor
        assert legacy_field not in baseline


def test_analytics_revenue_uses_currency_bearing_durable_events() -> None:
    event_model = (ROOT / "core/models/trackers/adsetpro_postback.py").read_text(encoding="utf-8")
    analytics = (ROOT / "core/analytics/performance.py").read_text(encoding="utf-8")

    assert "revenue: Mapped[Decimal | None]" in event_model
    assert "currency: Mapped[str | None]" in event_model
    assert "COUNT(DISTINCT currency)" in analytics
    assert "SUM(revenue)" in analytics
