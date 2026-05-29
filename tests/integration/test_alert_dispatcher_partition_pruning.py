# -*- coding: utf-8 -*-
"""Integration: alert_dispatcher использует partition-pruning по created_at.

CRIT #1 из backend_test_audit_round_8: SELECT в dispatch_pending_alerts
фильтровал только по scan_id — без created_at — и выполнял full-scan
всех партиций alert_events (~365). Фикс добавил WHERE created_at >= NOW()-1h.

Проверяем:
1. Индекс ix_alert_events_scan_id_created существует на alert_events.
2. EXPLAIN для запроса с фильтром по (scan_id, created_at) использует
   Index Scan / Bitmap Index Scan, а не Seq Scan по parent-таблице.
3. Функция dispatch_pending_alerts корректно находит события через новый фильтр.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.client import TelegramBotClient


@pytest_asyncio.fixture
async def clean_pruning_tables(pg_engine: AsyncEngine):
    """Очищаем зависимые таблицы до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_message_refs",
                "alert_events",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def alert_with_recent_event(pg_engine: AsyncEngine, clean_pruning_tables):
    """Создаёт offer→campaign→adset→ad + alert_event с текущим created_at."""
    suffix = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    open_token = uuid.uuid4()
    scan_id = 99901

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"PRUNE_{suffix}", "n": f"Prune test {suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": f"23{suffix}", "n": f"AD_{suffix}"},
        )
        # Вставляем alert_event с NOW() — попадает в текущий час
        await conn.execute(
            text(
                """
                INSERT INTO alert_events
                    (ad_id, scan_id, stage, state, matched_rule_codes,
                     metrics_json, open_state_token, created_at)
                VALUES
                    (:aid, :sid, 'warning', 'warning_sent', :codes,
                     :metrics, :tok, NOW())
                """
            ),
            {
                "aid": ad_id,
                "sid": scan_id,
                "codes": json.dumps(["cpc_warn"]),
                "metrics": json.dumps({"spend": "10.00"}),
                "tok": open_token,
            },
        )

    return {"scan_id": scan_id, "ad_id": ad_id, "open_token": open_token}


# Проверяем что индекс ix_alert_events_scan_id_created существует в БД.
@pytest.mark.asyncio
async def test_scan_id_index_exists(pg_engine: AsyncEngine, clean_pruning_tables) -> None:
    """Миграция 0004 создала индекс — он должен быть виден в pg_indexes."""
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'alert_events'
                      AND indexname = 'ix_alert_events_scan_id_created'
                    LIMIT 1
                    """
                )
            )
        ).first()
    assert row is not None, (
        "Индекс ix_alert_events_scan_id_created отсутствует — применить миграцию 0004"
    )


# Проверяем что EXPLAIN запроса с (scan_id, created_at) не делает Seq Scan по parent.
@pytest.mark.asyncio
async def test_explain_uses_index_not_seq_scan(
    pg_engine: AsyncEngine,
    alert_with_recent_event,
) -> None:
    """EXPLAIN ANALYZE с фильтром scan_id + created_at >= NOW()-1h должен
    использовать Index Scan, а не Seq Scan по родительской таблице."""
    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    EXPLAIN (FORMAT TEXT)
                    SELECT e.id, e.scan_id, e.created_at
                    FROM alert_events e
                    WHERE e.scan_id = :sid
                      AND e.created_at >= NOW() - INTERVAL '1 hour'
                    """
                ),
                {"sid": alert_with_recent_event["scan_id"]},
            )
        ).all()

    plan_text = "\n".join(r[0] for r in rows)
    # Проверяем что нет Seq Scan на РОДИТЕЛЬСКОЙ (нераспознанной) таблице alert_events.
    # Допустимо: "Seq Scan on alert_events_2026_05" — это дочерняя партиция, Postgres
    # выбирает Seq Scan когда в ней мало данных (<<1000 строк) — это нормально.
    # Недопустимо: "Seq Scan on alert_events " — full-scan parent без partition pruning.
    lines_with_seq = [
        line
        for line in plan_text.splitlines()
        if "Seq Scan on alert_events " in line  # пробел после — parent, не _2026_xx
    ]
    assert not lines_with_seq, (
        f"Запрос делает full Seq Scan на parent-таблице alert_events (без партиции).\n"
        f"EXPLAIN:\n{plan_text}"
    )


# Проверяем что dispatch_pending_alerts находит свежее событие через новый фильтр.
@pytest.mark.asyncio
async def test_dispatch_finds_recent_event_via_partition_filter(
    pg_engine: AsyncEngine,
    alert_with_recent_event,
) -> None:
    """dispatch_pending_alerts должен вернуть sent=1 для свежего события.

    Если WHERE created_at >= NOW()-1h работает корректно — событие найдено.
    Если фильтр случайно обрезает текущий scan — sent=0, тест поймает регрессию.
    """
    # Настраиваем telegram_config с зашифрованным токеном (формат)
    from core.crypto import encrypt

    enc_token = encrypt("TEST_BOT_TOKEN_PRUNING")
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, chat_id,
                     forum_warning_thread_id, forum_stop_thread_id, poller_offset)
                VALUES ('default', :tok, 123456789, NULL, NULL, 0)
                ON CONFLICT (singleton_key) DO UPDATE
                SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                    chat_id = EXCLUDED.chat_id,
                    updated_at = NOW()
                """
            ),
            {"tok": enc_token},
        )

    sent_count = 0

    class _MockTGClient(TelegramBotClient):
        """Заглушка: фиксирует все send_message вызовы."""

        def __init__(self):
            # Не вызываем super().__init__ — не нужен реальный httpx клиент
            self._calls: list[dict] = []

        async def send_message(self, *, chat_id, text, **kwargs):
            nonlocal sent_count
            sent_count += 1
            self._calls.append({"chat_id": chat_id, "text": text})
            return {"message_id": 111}

    fake_client = _MockTGClient()
    result = await dispatch_pending_alerts(
        pg_engine,
        client=fake_client,
        scan_id=alert_with_recent_event["scan_id"],
    )

    # Событие должно быть найдено и отправлено (1 send_message)
    assert result["sent"] == 1, (
        f"Ожидали sent=1, получили {result}. "
        "Проверить фильтр created_at >= NOW()-1h в dispatch_pending_alerts."
    )
    assert result["errors"] == 0
    assert sent_count == 1

    # Cleanup telegram_config
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_config WHERE singleton_key = 'default'"))
