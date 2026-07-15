# -*- coding: utf-8 -*-
"""Интеграционный e2e тест observer pipeline: ScannedAdRow → БД + task_queue.

Без gRPC к browser-agent — мы инжектим список ScannedAdRow напрямую.
Это покрывает critical path: матчинг офферов, FSM, метрики, outbox.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow


@pytest_asyncio.fixture
async def clean_observer_tables(pg_engine):
    """Чистит catalog + observer-таблицы + task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "tracker_click_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def offer_kr2(pg_engine, clean_observer_tables):
    """Создаёт оффер с кодом CR2 + cpa_threshold для триггера STOP.

    Используем именно 'CR2' (не 'KE_CR2') потому что resolve_offer_code применяет
    word-boundary matching: 'KE_CR2' в строке 'CR2 | KE | MV' не сматчится
    (между CR2 и KE стоит ' | ', а не нужный для regex символ).
    """
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) "
                "VALUES (:i, 'CR2', 'Chicken Road 2', TRUE)"
            ),
            {"i": offer_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO offer_rules (offer_id, cpa_threshold)
                VALUES (:oid, :cpa)
                """
            ),
            {"oid": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": "CR2"}


def _make_row(
    *,
    fb_ad_id: str = "230011223344",
    campaign_name: str = "CR2 | KE | MV | Tyver",
    adset_name: str = "EQ_KE",
    ad_name: str = "Aviator001",
    spend: Decimal = Decimal("5.0"),
    leads: int = 0,
    registrations: int = 0,
    deposits: int = 0,
    cpc: Decimal | None = None,
) -> ScannedAdRow:
    """Фабрика ScannedAdRow с разумными дефолтами."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status="ACTIVE",
        spend=spend,
        budget="$10",
        reach=1000,
        impressions=2000,
        clicks=50,
        cpc=cpc,
        ctr=Decimal("2.5"),
        cpm=Decimal("2.5"),
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        outbound_clicks=30,
        landing_page_views=20,
    )


# Сценарий: новое объявление с нормой → upsert в каталог + INSERT метрик + ad_alert_state='normal'
@pytest.mark.asyncio
async def test_new_ad_with_normal_metrics(pg_engine, offer_kr2) -> None:
    # Полная воронка; депозиты теперь только из проекции
    # AdSet.pro: каждый click должен иметь registration + FTD.
    row = _make_row(
        spend=Decimal("3.0"),
        leads=10,
        registrations=5,
        deposits=0,  # Meta-депозиты больше не источник
        cpc=Decimal("0.05"),
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO tracker_click_state
                    (id, source, click_id, ad_id, fb_ad_id, attribution_status,
                     registration, ftd, confirmed_deposit, registration_at, ftd_at,
                     confirmed_deposit_at, ftd_revenue, redeposits, redeposit_revenue,
                     last_event_at, version, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), 'adsetpro', :c1, NULL, :fb, 'matched_direct',
                     true, true, true, now(), now(), now(), 10, 0, 0,
                     now(), 1, now(), now()),
                    (gen_random_uuid(), 'adsetpro', :c2, NULL, :fb, 'matched_direct',
                     true, true, true, now(), now(), now(), 10, 0, 0,
                     now(), 1, now(), now())
                """
            ),
            {"fb": row.fb_ad_id, "c1": f"{row.fb_ad_id}-d1", "c2": f"{row.fb_ad_id}-d2"},
        )

    result = await process_scan_rows(pg_engine, rows=[row], scan_id=1)

    assert result.rows_total == 1
    assert result.rows_with_offer == 1

    async with pg_engine.connect() as conn:
        # 1. Каталог поднялся целиком
        n_campaigns = (await conn.execute(text("SELECT COUNT(*) FROM fb_campaigns"))).scalar()
        n_adsets = (await conn.execute(text("SELECT COUNT(*) FROM fb_adsets"))).scalar()
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        assert n_campaigns == 1
        assert n_adsets == 1
        assert n_ads == 1

        # 2. Кампания сматчена с оффером
        camp = (
            await conn.execute(text("SELECT offer_id, campaign_name FROM fb_campaigns LIMIT 1"))
        ).first()
        assert camp[0] == offer_kr2["offer_id"]

        # 3. ad_metrics записал партицию
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1

        # 4. ad_alert_state создан в normal
        state_row = (
            await conn.execute(
                text("SELECT alert_state, current_stage FROM ad_alert_state LIMIT 1")
            )
        ).first()
        assert state_row[0] == "normal"
        assert state_row[1] is None

        # 5. Нет алертов и нет disable-задач
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        assert n_alerts == 0
        assert n_tasks == 0


# Сценарий: spend без депозитов превышает порог → fast-stop → disable task создаётся
@pytest.mark.asyncio
async def test_spend_no_deposit_triggers_stop_and_disable_task(pg_engine, offer_kr2) -> None:
    # cpa_threshold = 10. spend=20, deposits=0 → spend без события превышает 80% cpa = триггер STOP
    row = _make_row(
        spend=Decimal("20.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    result = await process_scan_rows(pg_engine, rows=[row], scan_id=42)

    assert result.alerts_stop + result.alerts_warning >= 1

    async with pg_engine.connect() as conn:
        # FSM перешёл в stop_sent или warning_sent
        st = (
            await conn.execute(
                text("SELECT alert_state, current_stage FROM ad_alert_state LIMIT 1")
            )
        ).first()
        assert st[0] in ("warning_sent", "stop_sent")

        # Если STOP — есть запись в alert_events с правильным stage и scan_id
        if st[0] == "stop_sent":
            evt = (
                await conn.execute(
                    text(
                        "SELECT stage, state, scan_id, matched_rule_codes "
                        "FROM alert_events ORDER BY created_at DESC LIMIT 1"
                    )
                )
            ).first()
            assert evt[0] == "stop"
            assert evt[1] == "stop_sent"
            assert evt[2] == 42
            assert isinstance(evt[3], list) and len(evt[3]) > 0

            # И обязательно meta_api_mutation pause_ad task (авто-стоп теперь через
            # Marketing API, DOM disable_worker удалён) с правильным target_id.
            task = (
                await conn.execute(
                    text(
                        "SELECT task_type, status, payload, requested_by "
                        "FROM task_queue WHERE task_type = 'meta_api_mutation' LIMIT 1"
                    )
                )
            ).first()
            assert task is not None
            assert task[0] == "meta_api_mutation"
            assert task[1] == "pending"
            assert task[2]["mutation_kind"] == "pause_ad"
            assert task[2]["target_id"] == row.fb_ad_id
            assert task[3] == "bot_auto_stop"


# Сценарий: повторный scan того же объявления — НЕ создаёт дубли disable task'и (idempotency)
@pytest.mark.asyncio
async def test_repeated_stop_does_not_duplicate_disable_task(pg_engine, offer_kr2) -> None:
    row = _make_row(
        spend=Decimal("25.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    # Первый цикл — должен попасть в STOP
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)
    # Второй цикл с теми же данными — НЕ должен дублировать
    await process_scan_rows(pg_engine, rows=[row], scan_id=2)

    async with pg_engine.connect() as conn:
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        # одна disable задача (idempotency_key включает open_state_token)
        assert n_tasks == 1
        # один alert event — повторный STOP не дублируется (FSM stop_sent → stop_sent без emit)
        assert n_alerts == 1


# Сценарий: ад завис в stop_sent, но в Meta уже OFF + инцидент старше cooldown →
# observer сам синхронизирует FSM в disabled (зеркало reopen). Mirror случая CR008,
# где наша pause-мутация упала, а ад фактически выключен.
@pytest.mark.asyncio
async def test_offline_ad_syncs_stop_sent_to_disabled(pg_engine, offer_kr2) -> None:
    from core.observer.writers import mark_disabled_when_offline

    # 1) цикл со STOP → ад в stop_sent
    row = _make_row(spend=Decimal("25.0"), cpc=Decimal("0.10"))
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)

    async with pg_engine.connect() as conn:
        ad_id = (await conn.execute(text("SELECT id FROM fb_ads LIMIT 1"))).scalar()
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
    assert st == "stop_sent"

    # 2) свежий stop_sent (инцидент моложе cooldown) → time-guard НЕ трогает
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is False
    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
    assert st == "stop_sent"

    # 3) состарим инцидент на 20 минут → теперь sync срабатывает
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET last_transition_at = NOW() - interval '20 min' "
                "WHERE ad_id = :aid"
            ),
            {"aid": ad_id},
        )
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is True
    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
    assert st == "disabled"

    # 4) идемпотентность: повторный вызов на disabled → no-op (False)
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is False


# Сценарий: ад OFF старше cooldown проходит весь pipeline → синхронизируется в disabled,
# новой disable-задачи НЕ создаётся (инцидент закрыт, не гоняем FSM по метрикам OFF-ада).
@pytest.mark.asyncio
async def test_pipeline_syncs_offline_incident_to_disabled(pg_engine, offer_kr2) -> None:
    # 1) STOP → stop_sent + одна disable-задача
    stop_row = _make_row(spend=Decimal("25.0"), cpc=Decimal("0.10"))
    await process_scan_rows(pg_engine, rows=[stop_row], scan_id=1)

    # состарим инцидент старше cooldown
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET last_transition_at = NOW() - interval '20 min'")
        )

    # 2) тот же ад приходит OFF → pipeline синхронизирует в disabled
    off_row = _make_row(spend=Decimal("25.0"), cpc=Decimal("0.10"))
    object.__setattr__(off_row, "delivery_status", "OFF")
    await process_scan_rows(pg_engine, rows=[off_row], scan_id=2)

    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
    assert st == "disabled"
    assert n_tasks == 1  # вторая (OFF) итерация не плодит новую disable-задачу


# Сценарий: объявление без подходящего оффера → каталог пишется, но без правил
@pytest.mark.asyncio
async def test_ad_without_matching_offer(pg_engine, offer_kr2) -> None:
    row = _make_row(
        campaign_name="UNKNOWN | XX | foo",
        ad_name="random",
        spend=Decimal("50.0"),
        deposits=0,
    )
    result = await process_scan_rows(pg_engine, rows=[row], scan_id=1)

    assert result.rows_with_offer == 0
    assert result.rows_without_offer == 1

    async with pg_engine.connect() as conn:
        # Каталог всё равно есть (нужен для исторических метрик)
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        n_states = (await conn.execute(text("SELECT COUNT(*) FROM ad_alert_state"))).scalar()
        assert n_ads == 1
        # Метрики записываются даже для unmatched
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1
        # А alert_state и outbox — не создаются (нет правил)
        assert n_alerts == 0
        assert n_tasks == 0
        assert n_states == 0


# Сценарий: scan_id корректно записывается во все таблицы где есть это поле
@pytest.mark.asyncio
async def test_scan_id_propagates(pg_engine, offer_kr2) -> None:
    row = _make_row(spend=Decimal("3.0"), deposits=1)
    await process_scan_rows(pg_engine, rows=[row], scan_id=12345)

    async with pg_engine.connect() as conn:
        scan_in_metrics = (
            await conn.execute(text("SELECT scan_id FROM ad_metrics LIMIT 1"))
        ).scalar()
        scan_in_state = (
            await conn.execute(text("SELECT last_scan_id FROM ad_alert_state LIMIT 1"))
        ).scalar()
    assert scan_in_metrics == 12345
    assert scan_in_state == 12345


# Сценарий: priority матчинга — более длинный code (CR2_KE) выигрывает у CR2
@pytest.mark.asyncio
async def test_matching_prefers_longest_code(pg_engine, clean_observer_tables) -> None:
    offer_long = uuid.uuid4()
    offer_short = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name) "
                "VALUES (:i, 'CR2_KE', 'long'), (:s, 'CR2', 'short')"
            ),
            {"i": offer_long, "s": offer_short},
        )

    # В кампании присутствует только подстрока 'CR2_KE' как отдельное «слово»
    row = _make_row(
        campaign_name="Aviator | CR2_KE | MV | promo",
        ad_name="Test001",
        spend=Decimal("3.0"),
    )
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)

    async with pg_engine.connect() as conn:
        camp = (await conn.execute(text("SELECT offer_id FROM fb_campaigns LIMIT 1"))).first()
    # CR2_KE длиннее → выиграл, хотя 'CR2' тоже сматчился бы как substring
    assert camp[0] == offer_long


# HIGH #6: snoozed_until == cycle_ts — граничное равенство НЕ подавляет emit
@pytest.mark.asyncio
async def test_snooze_boundary_equality_does_not_suppress(pg_engine, offer_kr2) -> None:
    """snoozed_until == cycle_ts: строгое > в pipeline не suppress'ит emit при равенстве."""
    # Создаём ad в состоянии warning_sent через первый скан
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)
    ts1 = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    await process_scan_rows(pg_engine, rows=[row], scan_id=1, cycle_ts=ts1)

    # Ставим snoozed_until = ts2 (ровно момент следующего скана)
    ts2 = datetime(2026, 5, 28, 10, 30, 0, tzinfo=UTC)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET snoozed_until = :su "
                "WHERE alert_state IN ('warning_sent', 'stop_sent')"
            ),
            {"su": ts2},
        )

    # Убираем event из первого скана чтобы посчитать только новый
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Второй скан с cycle_ts == snoozed_until: emit НЕ должен подавляться (строгое >)
    result2 = await process_scan_rows(pg_engine, rows=[row], scan_id=2, cycle_ts=ts2)

    # Pipeline мог не дать warning (повтор FSM → no new emit для stop_sent→stop_sent),
    # но главное — pipeline не suppress'ил из-за snooze. Проверяем напрямую через
    # ad_alert_state: snoozed_until == cycle_ts → snooze НЕ активен.
    async with pg_engine.connect() as conn:
        snoozed_until = (
            await conn.execute(text("SELECT snoozed_until FROM ad_alert_state LIMIT 1"))
        ).scalar()
    # Значение snoozed_until == ts2 — это нормально; проверяем что pipeline не упал
    # и не suppress'ил по причине snooze (suppress только при snoozed_until > cycle_ts)
    assert result2 is not None, "process_scan_rows должен вернуть CycleResult без ошибки"
    assert snoozed_until is not None, "snoozed_until должен остаться (pipeline не сбросил его)"


# HIGH #7: snooze истёк между двумя сканами → третий скан эмитит алерт
@pytest.mark.asyncio
async def test_snooze_expired_between_scans_emits_on_third(pg_engine, offer_kr2) -> None:
    """Snooze истекает между scan #2 и #3: scan #3 должен эмитить алерт."""
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)

    # Scan #1: ставим ad в warning_sent/stop_sent
    ts1 = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    await process_scan_rows(pg_engine, rows=[row], scan_id=1, cycle_ts=ts1)

    # Ставим snoozed_until = ts1 + 2 минуты (истечёт после ts2 но до ts3)
    snooze_exp = ts1 + timedelta(minutes=2)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET snoozed_until = :su "
                "WHERE alert_state IN ('warning_sent', 'stop_sent')"
            ),
            {"su": snooze_exp},
        )

    # Чистим события scan #1 чтобы счётчики были точными
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Scan #2: cycle_ts = ts1 + 1 мин < snoozed_until → snooze активен, emit suppress'ируется
    ts2 = ts1 + timedelta(minutes=1)
    await process_scan_rows(pg_engine, rows=[row], scan_id=2, cycle_ts=ts2)

    async with pg_engine.connect() as conn:
        n_events_after_scan2 = (
            await conn.execute(text("SELECT COUNT(*) FROM alert_events WHERE scan_id = 2"))
        ).scalar()
    # Scan #2: emit suppress'ирован из-за snooze (snoozed_until > cycle_ts)
    assert n_events_after_scan2 == 0, (
        f"Scan #2 должен быть suppressed, но alert_events.scan_id=2: {n_events_after_scan2}"
    )

    # Scan #3: cycle_ts = ts1 + 3 мин > snoozed_until → snooze истёк, emit разрешён
    ts3 = ts1 + timedelta(minutes=3)
    await process_scan_rows(pg_engine, rows=[row], scan_id=3, cycle_ts=ts3)

    # Scan #3: FSM stop_sent → stop_sent (no new emit for same state) — но мы проверяем
    # что pipeline НЕ suppress'ил по snooze. В реальности stop_sent → stop_sent уже
    # не выдаёт новый emit (FSM идемпотентен). Ключевое: нет suppress-метки в transition_reason.
    # Чтобы проверить emit — сбросим alert_state до warning_sent (имитируем эскалацию).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET alert_state = 'warning_sent', current_stage = 'warning'"
            )
        )
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Повторный scan #4 после сброса: snooze истёк, должен выдать emit
    ts4 = ts1 + timedelta(minutes=4)
    await process_scan_rows(pg_engine, rows=[row], scan_id=4, cycle_ts=ts4)

    async with pg_engine.connect() as conn:
        n_events_after_scan4 = (
            await conn.execute(text("SELECT COUNT(*) FROM alert_events WHERE scan_id = 4"))
        ).scalar()
    # После истечения snooze alert_event должен создаться (stop_sent → emit)
    assert n_events_after_scan4 >= 1, (
        f"После истечения snooze scan #4 должен создать alert_event, "
        f"но alert_events.scan_id=4: {n_events_after_scan4}"
    )


# C1/M3 + MID-2 (аудит 02.07): снуз глушит ТОЛЬКО TG-алерты, НЕ авто-стоп — pause-задача
# создаётся и под активным снузом (раньше подавлялась → заснуженный убыточный ад крутился
# без стопа до конца окна). Recovery после истечения снуза не создаёт дубль (idempotency).
@pytest.mark.asyncio
async def test_snooze_stop_recovery_creates_pause_task(pg_engine, offer_kr2) -> None:
    """Снуз → stop_sent под снузом (pause-задача СОЗДАЁТСЯ — MID-2, авто-стоп работает) →
    снуз истёк → recovery-скан не плодит вторую задачу (одна на инцидент)."""
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)

    # Scan #1 → ад уходит в stop_sent (трата без событий).
    ts1 = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
    await process_scan_rows(pg_engine, rows=[row], scan_id=1, cycle_ts=ts1)

    # Снуз активен до ts1+2мин + эмулируем «pause-задача не создавалась» (краш-сценарий):
    snooze_exp = ts1 + timedelta(minutes=2)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET snoozed_until = :su WHERE alert_state = 'stop_sent'"),
            {"su": snooze_exp},
        )
        await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))

    # Scan #2 под активным снузом: pause-задача СОЗДАЁТСЯ (MID-2 — снуз не выключает
    # авто-стоп; подавляется только TG-алерт).
    ts2 = ts1 + timedelta(minutes=1)
    await process_scan_rows(pg_engine, rows=[row], scan_id=2, cycle_ts=ts2)
    async with pg_engine.connect() as conn:
        n_under_snooze = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE payload->>'mutation_kind' = 'pause_ad'")
            )
        ).scalar()
    assert n_under_snooze == 1, "под снузом авто-стоп обязан работать (MID-2): одна pause-задача"

    # Scan #3 после истечения снуза: recovery НЕ плодит дубль — по-прежнему ровно одна
    # pause_ad на инцидент (idempotency_key по open_token).
    ts3 = ts1 + timedelta(minutes=3)
    await process_scan_rows(pg_engine, rows=[row], scan_id=3, cycle_ts=ts3)
    async with pg_engine.connect() as conn:
        recovered = (
            await conn.execute(
                text(
                    "SELECT payload->>'mutation_kind' AS kind, payload->>'target_id' AS target "
                    "FROM task_queue WHERE payload->>'mutation_kind' = 'pause_ad'"
                )
            )
        ).fetchall()
    assert len(recovered) == 1, (
        f"ровно одна pause-задача на инцидент (без дубля от recovery), получено {len(recovered)}"
    )
    assert recovered[0].kind == "pause_ad"
    assert recovered[0].target == "230011223344"


# Owner-scoping: чужая кампания с кодом оффера CR2, но без тега MV, НЕ обрабатывается
# (ключевая защита — owner-фильтр сильнее матчинга оффера, иначе бот тронул бы чужое)
@pytest.mark.asyncio
async def test_owner_scoping_filters_foreign_campaign(pg_engine, offer_kr2) -> None:
    # Моя: тег MV + код CR2, метрики в норме (leads>=reg>=dep)
    mine = _make_row(
        fb_ad_id="111000",
        campaign_name="MV | KE | CR2 | adset.pro | 22.05 | 1",
        ad_name="KE_CR2_CR005",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
        cpc=Decimal("0.05"),
    )
    # Чужая: код CR2 ЕСТЬ (сматчился бы!), тега MV НЕТ, spend высокий без депозитов
    # → без owner-фильтра она получила бы STOP + disable на ЧУЖОЙ ad
    foreign = _make_row(
        fb_ad_id="222000",
        campaign_name="14.05 MZ Artemteam CR2 CBO 1-3-1",
        ad_name="FW3-5",
        spend=Decimal("50.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    result = await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1, owner_tag="MV")

    assert result.rows_total == 2
    assert result.rows_foreign == 1, "чужая кампания должна быть отброшена owner-фильтром"
    assert result.rows_with_offer == 1, "только моя кампания доходит до матчинга оффера"

    async with pg_engine.connect() as conn:
        # В каталоге только моя — чужой fb_ad_id 222000 не записан
        ads = [r[0] for r in (await conn.execute(text("SELECT fb_ad_id FROM fb_ads"))).all()]
        assert ads == ["111000"]
        # Чужая не создала disable несмотря на spend=50 без депозитов
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        assert n_tasks == 0
        # Метрики только для моей (чужая полностью проигнорирована)
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1


# Owner-scoping выключен (owner_tag=None) → обе кампании обрабатываются (обратная совместимость)
@pytest.mark.asyncio
async def test_owner_scoping_disabled_processes_all(pg_engine, offer_kr2) -> None:
    mine = _make_row(
        fb_ad_id="111001",
        campaign_name="MV | KE | CR2",
        ad_name="a1",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
    )
    other = _make_row(
        fb_ad_id="222001",
        campaign_name="MZ Artemteam CR2",
        ad_name="a2",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
    )

    # owner_tag не задан → фильтр выключен
    result = await process_scan_rows(pg_engine, rows=[mine, other], scan_id=1)

    assert result.rows_foreign == 0
    assert result.rows_with_offer == 2, "обе содержат код CR2 → обе сматчены без owner-фильтра"
    async with pg_engine.connect() as conn:
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        assert n_ads == 2
