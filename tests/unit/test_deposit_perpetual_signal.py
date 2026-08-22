# -*- coding: utf-8 -*-
"""Депозит удерживает ветку «с депозитом» бессрочно (boundary 00:00 кабинетных суток).

Четыре инварианта:
1. Депозит подтверждён вчера → объявление остаётся на ветке «с депозитом» в 00:30 нового дня.
2. Депозитов не было никогда → строгая ветка (no-dep guardrails).
3. Данных о депозитах нет (tracker не знает этот ad) — это не подтверждённый ноль.
4. В алерте число депозитов — за сегодня, а не за всё время.
"""

from __future__ import annotations

from decimal import Decimal

from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow


def _make_row(**kw) -> ScannedAdRow:
    defaults = {
        "fb_ad_id": "120241979860890176",
        "campaign_name": "campaign",
        "adset_name": "adset",
        "ad_name": "DRC_CR2_CR015",
        "delivery_status": "ACTIVE",
        "spend": Decimal("0.00"),
        "clicks": 0,
        "cpc": None,
        "outbound_clicks": 0,
        "outbound_ctr": None,
        "landing_page_views": 0,
        "cost_per_landing_page_view": None,
        "cpm": None,
        "frequency": None,
        "leads": 0,
        "cost_per_lead": None,
        "registrations": 0,
        "cost_per_registration": None,
        "deposits": 0,
    }
    defaults.update(kw)
    return ScannedAdRow(**defaults)


def _make_ctx(**kw) -> RuleContext:
    defaults = {
        "currency": "USD",
        "currency_exponent": 2,
        "cpa_amount": Decimal("5.00"),
        "warning_percent_of_stop": Decimal("80"),
        "stop_percent_of_base": Decimal("100"),
    }
    defaults.update(kw)
    return RuleContext(**defaults)


# ─── Тест 1: load_ever_had_deposit_batch должна существовать ─────────────────


def test_ever_had_deposit_query_function_is_available() -> None:
    """Граница суток: pipeline должен опрашивать ever-had-deposit без окна.

    Функция load_ever_had_deposit_batch должна быть экспортирована из
    core.adset_pro.queries — именно её pipeline использует для определения
    ветки (with/without deposit), а не оконную load_external_deposits_batch.
    """
    import core.adset_pro.queries as q

    assert hasattr(q, "load_ever_had_deposit_batch"), (
        "load_ever_had_deposit_batch не найдена в core.adset_pro.queries — "
        "pipeline не может определить ever-had-deposit без временного окна"
    )


# ─── Тест 2: RuleContext должен хранить today_deposits ───────────────────────


def test_rule_context_has_today_deposits_field() -> None:
    """RuleContext должен содержать поле today_deposits — только для отображения.

    external_deposits = ever-had (ветка), today_deposits = за сегодня (счётчик в алерте).
    Поля разделены, чтобы «никогда не было депозита» ≠ «нет депозитов за сегодня».
    """
    ctx = _make_ctx()

    assert hasattr(ctx, "today_deposits"), (
        "today_deposits не найдено в RuleContext — алерт не может показывать "
        "счётчик депозитов за сегодня отдельно от ever-had флага"
    )


# ─── Тест 3: «нет данных» ≠ «подтверждённый ноль» ───────────────────────────


def test_no_tracker_data_differs_from_confirmed_zero_deposits() -> None:
    """Отсутствие данных трекера (нет строк) — не то же, что подтверждённый ноль.

    today_deposits=None — нет данных от трекера.
    today_deposits=0   — трекер вернул ноль (подтверждённый ноль за сегодня).
    Поле должно допускать None, и дефолт должен быть None, а не 0.
    """
    ctx_no_data = _make_ctx()  # pipeline не передаёт today_deposits → None (нет данных)

    assert hasattr(ctx_no_data, "today_deposits"), "today_deposits не найдено в RuleContext"
    assert ctx_no_data.today_deposits is None, (
        f"Дефолт today_deposits должен быть None (нет данных), "
        f"получили: {ctx_no_data.today_deposits!r}"
    )

    # Подтверждённый ноль явно передаётся из pipeline.
    ctx_confirmed_zero = _make_ctx(today_deposits=0)
    assert ctx_confirmed_zero.today_deposits == 0


# ─── Тест 4: алерт показывает today_deposits, а не total ever ────────────────


def test_deposit_alert_text_shows_today_count_not_total_ever() -> None:
    """В тексте алерта spend_with_dep_range — число депозитов за сегодня, не за всё время.

    Сценарий: 3 депозита подтверждено суммарно за всё время (external_deposits=3),
    но сегодня ни одного (today_deposits дефолт — None → отображается как 0/«нет»).
    Алерт должен показывать сегодняшний счётчик, иначе байер видит «3 депозита»
    в 02:00 и думает, что сегодня они уже были.
    """
    row = _make_row(spend=Decimal("4.00"))  # 80% от CPA=5 → deposit-branch hit
    ctx = _make_ctx(external_deposits=3)  # 3 ever, today_deposits дефолт (None/0)

    result = evaluate_stop_rules(row, ctx)

    dep_hits = [
        h for h in (*result.stop_hits, *result.warning_hits) if h.code == "spend_with_dep_range"
    ]
    assert dep_hits, (
        "При external_deposits=3 ожидался хит spend_with_dep_range, но ни одного нет — "
        "объявление должно быть на ветке 'с депозитом'"
    )

    summary = dep_hits[0].summary
    # До фикса: _evaluate_deposit_stage использует ctx.external_deposits=3 →
    # summary содержит "3 депозита". После фикса — today_deposits=None/0 → "депозитов нет".
    assert "3 депозита" not in summary, (
        f"Алерт показывает суммарное число депозитов (3), а должен — сегодняшнее (0/нет). "
        f"summary: {summary!r}"
    )


# ─── Инвариант на самой границе: вчерашний депозит держит мягкую ветку ───────


def test_yesterdays_deposit_keeps_soft_branch_at_midnight_boundary() -> None:
    """00:30 нового кабинетного дня, сегодня депозитов ноль, вчера был депозит.

    Признак берётся без окна, поэтому объявление остаётся на ветке «с
    депозитом»: срабатывает кап по расходу с депозитом, а не строгое правило
    расхода без депозита. Именно эта подмена ветки на границе суток и стоит
    найденной связки.
    """
    row = _make_row(spend=Decimal("4.00"), clicks=40, registrations=6)

    hot = evaluate_stop_rules(
        row,
        _make_ctx(external_deposits=1, today_deposits=0),
    )
    cold = evaluate_stop_rules(
        row,
        _make_ctx(external_deposits=0, today_deposits=0),
    )

    assert "spend_no_dep_range" not in hot.matched_rule_codes, (
        "объявление с подтверждённым депозитом попало под строгое правило "
        f"расхода без депозита: {hot.matched_rule_codes}"
    )
    assert "regs_no_dep_stop" not in hot.matched_rule_codes, (
        "объявление с подтверждённым депозитом попало под правило регистраций "
        f"без депозита: {hot.matched_rule_codes}"
    )
    assert hot.matched_rule_codes != cold.matched_rule_codes, (
        f"ветка не различает объявление с депозитом и без него: {hot.matched_rule_codes}"
    )


def test_pipeline_takes_deposit_signal_without_a_daily_window() -> None:
    """Признак ветки грузится без окна, счётчик для показа — с окном.

    Гард по исходнику: подмена ever-had загрузки на оконную возвращает дефект
    целиком и при этом не роняет ни один тест уровня правил, потому что там
    признак приходит уже готовым числом. Ловится только здесь.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "core" / "observer" / "pipeline.py").read_text(
        encoding="utf-8"
    )

    assert "external_deposits = await load_ever_had_deposit_batch(" in source, (
        "признак «есть депозит» снова считается по окну кабинетных суток — "
        "на границе 00:00 объявление с депозитом станет холодным"
    )
    assert "today_deposits = await load_external_deposits_batch(" in source, (
        "счётчик депозитов для показа обязан оставаться оконным: иначе "
        "«депозиты есть (3)» в 02:00 при сегодняшнем нуле врёт оператору"
    )
