from __future__ import annotations

import re
from pathlib import Path

from core.observer.writers import _incident_lines, _incident_risk, _incident_summary
from core.telegram.notification_renderer import render_notification
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_MONEY_PATHS = (
    "apps/digest_scheduler/main.py",
    "apps/health_watchdog/main.py",
    "core/dashboard/cabinet_spend.py",
    "core/meta_api/autostop_alert.py",
    "core/meta_api/shadow_spend.py",
    "core/models/meta_api/diagnostics.py",
    "core/models/observer/ad_metrics.py",
    "core/observer/pipeline.py",
    "core/observer/writers.py",
    "core/telegram/digest_builder.py",
    "core/telegram/notification_renderer.py",
)
MONEY_FORMAT_PATHS = tuple(
    path
    for path in ACTIVE_MONEY_PATHS
    if path
    not in {
        "core/models/meta_api/diagnostics.py",
        "core/models/observer/ad_metrics.py",
        "core/telegram/notification_renderer.py",
    }
)


def test_active_notification_money_paths_have_no_legacy_unit_assumptions() -> None:
    sources = {path: (ROOT / path).read_text(encoding="utf-8") for path in ACTIVE_MONEY_PATHS}
    forbidden_patterns = {
        "hardcoded USD": re.compile(r"\bUSD\b"),
        "legacy spend_usd": re.compile(r"\bspend_usd\b"),
        "legacy cent names": re.compile(r"\b\w*_cents\b|\b\w*_CENTS\b"),
        "implicit multiply by 100": re.compile(r"\*\s*100\b|\b100\s*\*"),
        "implicit divide by 100": re.compile(r"/\s*100\b"),
    }

    violations: list[str] = []
    for path, source in sources.items():
        for label, pattern in forbidden_patterns.items():
            if pattern.search(source):
                violations.append(f"{path}: {label}")
    for path in MONEY_FORMAT_PATHS:
        if "$" in sources[path]:
            violations.append(f"{path}: currency symbol")
    assert violations == []


def test_observer_metric_money_evidence_has_explicit_currency_and_precision() -> None:
    model_source = (ROOT / "core/models/observer/ad_metrics.py").read_text(encoding="utf-8")
    writer_source = (ROOT / "core/observer/writers.py").read_text(encoding="utf-8")

    assert "currency: Mapped[str | None] = mapped_column(String(3)" in model_source
    assert "spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 3)" in model_source
    assert "ad_metrics currency must be confirmed" in writer_source
    assert "(ad_id, cycle_ts, scan_id, currency," in writer_source


def test_observer_notification_money_facts_require_and_render_currency() -> None:
    metrics = {
        "spend": "18.40",
        "_hits": [
            {
                "code": "cpl_stop",
                "stage": "stop",
                "value": "9.56",
                "threshold": "3.00",
            }
        ],
    }

    assert _incident_lines(metrics, currency="KES") == ["Spend 18.40 KES"]
    assert (
        _incident_summary(
            metrics,
            ("cpl_stop",),
            currency="KES",
        )
        == "CPL_STOP: 9.56 KES при пороге 3.00 KES"
    )
    assert _incident_lines(metrics, currency=None) == ["Spend не показан: валюта не подтверждена"]
    assert "денежные значения не подтверждены" in _incident_summary(
        metrics,
        ("cpl_stop",),
        currency=None,
    )
    assert _incident_risk(("spend_no_dep_range",)) == ("расход без первого депозита")


def test_money_card_escapes_dynamic_values_and_stays_within_telegram_limit() -> None:
    event = NotificationEventSpec(
        event_type="daily_digest",
        severity="warning",
        audience="all",
        facts=NotificationCardFacts(
            title="<Digest & money>",
            summary="Spend 123456789.123 KWD · warning 2 · critical 1",
            lines=[f"Топ: <unsafe & ad {index}> · 999999.123 KWD" for index in range(5)],
        ),
        dedupe_key="money-card-escaping",
    )

    rendered = render_notification(event)

    assert len(rendered.text) <= 700
    assert "<unsafe" not in rendered.text
    assert "&lt;unsafe &amp; ad" in rendered.text
    assert "KWD" in rendered.text
