# Telegram Волна 4 — cleanup + дайджест-спенд money-fix · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Починить занижение спенда в дайджесте (CRIT-1) + удалить накопленный tech-debt волн 1-2 (dead-code снуза, мёртвые fallback-параметры, forum-колонки) без регресса.

**Architecture:** Дайджест-total переходит на `latest_per_ad_per_day_cte` (как dashboard/history). Fallback-ветки `engine=None` (волна 2) выпиливаются — engine всегда в проде. forum-колонки дропаются миграцией.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x, Alembic, pytest.

## Global Constraints

- Русский в комментариях/логах + русский комментарий над каждым тестом.
- Ruff line-length=100, py312, E/F/I/B/ASYNC; чисто.
- НЕ pytest на боевой :5433. Unit мокаются; integration — изолированная pg_engine (fb_stop_bot_test).
- ad_metrics КУМУЛЯТИВНЫ (сбрасываются в cabinet-полночь) — НЕ суммировать сырые snapshot'ы наивно (это и есть CRIT-1).
- Money-путь: дайджест-спенд должен показывать РЕАЛЬНУЮ сумму окна, не latest snapshot.
- Один коммит на задачу.

---

### Task 1: Дайджест-спенд CRIT-1 (MONEY)

**Files:**
- Modify: `core/telegram/digest_builder.py` (`_top_ads_and_total_spend` total-запрос + `DigestPayload` поле)
- Modify: `core/telegram/digest_renderer.py` (подпись окна)
- Test: `tests/unit/test_digest_spend_aggregation.py` (или integration, если нужна БД)

**Interfaces:**
- Consumes: `latest_per_ad_per_day_cte(*, cte_alias, columns=..., from_param, to_param) -> str` (из `core/dashboard/metric_aggregation.py:103`), возвращает SQL-фрагмент `<alias> AS (SELECT DISTINCT ON (m.ad_id, date_trunc('day', m.cycle_ts)) ...)`.
- Produces: `DigestPayload.total_spend_window_usd` (переименование из `total_spend_24h_usd`).

**Контекст:** `_top_ads_and_total_spend` (digest_builder.py:162-252). Total-запрос (стр.216-235) сейчас `DISTINCT ON (m.ad_id) ORDER BY cycle_ts DESC` → SUM = только последний snapshot per ad → при окне 09:00-09:00 UTC, пересекающем cabinet-полночь, теряется спенд дня N-1. Топ-строки (стр.184-213) тоже latest-per-ad — но это для отображения топ-объявлений (latest приемлемо для ранжирования; total — нет). Чиним ТОЛЬКО total (money-критичен); топ оставляем latest-per-ad. `history_queries.py:45` — образец использования `latest_per_ad_per_day_cte`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/integration/test_digest_spend_aggregation.py
# -*- coding: utf-8 -*-
"""Дайджест-total суммирует спенд per-ad-per-day (не теряет день до cabinet-полуночи)."""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
import pytest_asyncio
from sqlalchemy import text
from core.telegram.digest_builder import _top_ads_and_total_spend


@pytest_asyncio.fixture
async def _seed_two_days(pg_engine):
    """Один ad, 2 snapshot: вчера 23:00 spend=80 (день N-1) + сегодня 01:00 spend=30 (день N, сброс)."""
    ad_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
    async with pg_engine.begin() as conn:
        for t in ("ad_metrics", "fb_ads", "fb_adsets", "fb_campaigns"):
            await conn.execute(text(f"DELETE FROM {t}"))
        cid = uuid.uuid4(); sid = uuid.uuid4()
        await conn.execute(text("INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) VALUES (:i,'c','CR2|KE',NOW())"), {"i": cid})
        await conn.execute(text("INSERT INTO fb_adsets (id, fb_adset_id, adset_name, fb_campaign_pk, last_seen_at) VALUES (:i,'s','EQ',:c,NOW())"), {"i": sid, "c": cid})
        await conn.execute(text("INSERT INTO fb_ads (id, fb_ad_id, ad_name, fb_adset_pk, last_seen_at) VALUES (:i,'900','Ad',:s,NOW())"), {"i": ad_id, "s": sid})
        # день N-1 (вчера 23:00): кумулятив 80
        await conn.execute(text("INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend) VALUES (gen_random_uuid(), :a, :ts, 80)"),
                           {"a": ad_id, "ts": now - timedelta(hours=3)})
        # день N (сегодня 01:00): кумулятив 30 (после сброса в полночь)
        await conn.execute(text("INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend) VALUES (gen_random_uuid(), :a, :ts, 30)"),
                           {"a": ad_id, "ts": now - timedelta(hours=1)})
    return {"window_start": now - timedelta(hours=24), "window_end": now}


# total = 80 (день N-1) + 30 (день N) = 110, НЕ 30 (наивный latest) и НЕ 110 случайно
@pytest.mark.asyncio
async def test_total_spend_sums_per_day(pg_engine, _seed_two_days):
    _top, total = await _top_ads_and_total_spend(
        pg_engine, window_start=_seed_two_days["window_start"],
        window_end=_seed_two_days["window_end"], limit=5)
    assert total == Decimal("110"), f"ожидалось 110 (80+30 per-day), получено {total}"
```
(FK-колонки сверить с реальной схемой.)

- [ ] **Step 2: Прогон — падает**

Run: `python -m pytest tests/integration/test_digest_spend_aggregation.py -q`
Expected: FAIL — total=30 (наивный latest per ad), не 110.

- [ ] **Step 3: Реализация**

В `digest_builder.py` импортировать `from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte`. Переписать total-запрос (стр.216-235): использовать per-day CTE и SUM по всем (ad, day):
```python
    cte = latest_per_ad_per_day_cte(cte_alias="per_ad_day", from_param="start", to_param="end")
    total_sql = text(f"WITH {cte} SELECT COALESCE(SUM(spend), 0) FROM per_ad_day")
    # params: start=window_start, end=window_end (имена должны совпасть с from_param/to_param)
```
(сверить имена bind-параметров с тем, что подставляет `latest_per_ad_per_day_cte` — `from_param`/`to_param` задают имена `:start`/`:end`.)
Переименовать `DigestPayload.total_spend_24h_usd` → `total_spend_window_usd` (dataclass стр.52 + присвоение стр.299). Обновить все ссылки (renderer).
В `digest_renderer.py:86-88` — поле `payload.total_spend_window_usd`; подпись окна (стр.63) — оставить «окно Nч» или указать диапазон (минимально: переименовать поле, текст «спенд» не трогать).

- [ ] **Step 4: Прогон зелёный + ruff + полный unit**

Run: `python -m pytest tests/integration/test_digest_spend_aggregation.py -q && python -m pytest tests/unit -q -k digest && ruff check core/telegram/digest_builder.py core/telegram/digest_renderer.py`
Expected: PASS (total=110), ruff clean, digest unit'ы зелёные (переименование поля не сломало).

- [ ] **Step 5: Commit**

```bash
git add core/telegram/digest_builder.py core/telegram/digest_renderer.py tests/integration/test_digest_spend_aggregation.py
git commit -m "fix(digest): спенд per-ad-per-day CTE (не занижать при cabinet-полуночи) — CRIT-1"
```

---

### Task 2: Dead-code снуза + stale docstrings + redundant condition

**Files:**
- Modify: `core/telegram/handlers/alerts.py` (удалить `handle_snz_callback` стр.117 + из `__all__` стр.152 + snz из файл-docstring стр.2,5)
- Modify: `core/telegram/handlers/router.py` (убрать `snz` из docstring `_dispatch_callback_query` стр.80; убрать redundant `recipient and` стр.256)
- Test: `tests/unit/test_telegram_renderer.py` / `test_tg_acl_guards.py` (если ссылаются на snz — адаптировать)

- [ ] Шаги: удалить `handle_snz_callback` + экспорт + docstring-упоминания snz; в router.py:256 `if needs_owner and not (recipient and recipient.is_owner())` → `if needs_owner and not recipient.is_owner()` (recipient гарантированно не None после гейта стр.244); docstring стр.80 убрать snz из списка action. Полный unit зелёный (ничего не импортирует handle_snz_callback). ruff. Commit.

```bash
git commit -m "refactor(telegram): удалить dead-code снуза + stale docstrings + redundant ACL-condition"
```

---

### Task 3: Выпил мёртвых fallback-параметров (engine-gate)

**Files:**
- Modify: `core/meta_api/autostop_alert.py` (`maybe_alert_autostop_channel_down`: убрать `tg_client/chat_id/thread` + fallback-ветку стр.195-208)
- Modify: `apps/meta_api_worker/main.py` (`AutostopAlertContext`: убрать `tg_client/chat_id/thread_id` поля стр.97-111; `_load_tg` стр.672-689 — упростить/удалить, не создавать orphaned client)
- Modify: `apps/health_watchdog/main.py` (`_maybe_alert_with_dedup`: убрать fallback-ветку стр.338-340; `_send_alert`/`_load_tg` — удалить если только для fallback)
- Modify: `apps/enable_recommendation_worker/main.py` (`send_alert`: убрать `chat_id/thread` + fallback стр.330-347; `_default_tg_factory` — упростить)
- Modify: `tests/unit/test_worker_notify_recipients.py` (3 fallback-теста стр.110/198/282 — переписать на engine-путь или удалить как дублирующие)

**Контекст:** engine ВСЕГДА передаётся в проде (подтверждено opus-review волны 2). Fallback `engine is None` — мёртв, живёт только ради этих 3 тестов. Выпиливаем параметры + ветки + адаптируем тесты на recipients-only (engine задан).

- [ ] Шаги: убрать fallback-ветки + неиспользуемые параметры из 4 функций; убедиться, что прод-вызовы (передающие engine) не сломаны; переписать 3 fallback-теста на engine-путь (мок notify_recipients/load_active_recipients) ИЛИ удалить, если поведение покрыто другими; полный unit зелёный; ruff. Commit.

```bash
git commit -m "refactor(workers): выпилить мёртвые fallback-параметры (engine-gate всегда задан)"
```

---

### Task 4: Консолидация digest recipients

**Files:**
- Modify: `apps/digest_scheduler/main.py` (`_load_active_recipients` стр.98-117 → использовать `core.telegram.service.load_active_recipients`)

**Контекст:** локальный `_load_active_recipients` возвращает `list[tuple[chat_id, None]]`; core `load_active_recipients` возвращает `list[Recipient]`. `_send_digest_to_recipients` (стр.179) использует результат — адаптировать на `Recipient.chat_id`.

- [ ] Шаги: импортировать `load_active_recipients` из service; удалить локальный дубль; в `_send_digest_to_recipients` брать `r.chat_id` из Recipient; тест digest-рассылки зелёный; ruff. Commit.

```bash
git commit -m "refactor(digest): использовать service.load_active_recipients (убрать дубль запроса)"
```

---

### Task 5: Миграция DROP forum-колонок

**Files:**
- Create: `migrations/versions/0024_drop_forum_thread_columns.py`
- Test: integration (миграция применяется/откатывается) — опц., или ручная проверка

**Контекст:** head `0023_telegram_invite_role`. Делать ПОСЛЕ Task 3 (код точно не читает forum). `chat_id` НЕ трогать.

- [ ] **Step 1: Миграция**

```python
"""DROP мёртвых forum_*_thread_id из telegram_config (DM-модель, волна 2)."""
from alembic import op
import sqlalchemy as sa

revision = "0024_drop_forum_thread_columns"
down_revision = "0023_telegram_invite_role"
branch_labels = None
depends_on = None

_COLS = ["forum_warning_thread_id", "forum_stop_thread_id", "forum_enable_thread_id",
         "forum_ops_thread_id", "forum_digest_thread_id"]


def upgrade() -> None:
    for c in _COLS:
        op.drop_column("telegram_config", c)


def downgrade() -> None:
    for c in _COLS:
        op.add_column("telegram_config", sa.Column(c, sa.Integer(), nullable=True))
```

- [ ] Шаги: написать миграцию; проверить, что схема/ORM-модель (telegram_config.py) больше не объявляет forum-колонки (волна 2 убрала из ORM); прогнать любой integration на изолированной БД (схема создаётся без forum-колонок); ruff. Commit.

```bash
git commit -m "migration(0024): DROP мёртвых forum_*_thread_id из telegram_config"
```

---

### Task 6: Финальная верификация + broad-review

- [ ] `python -m pytest tests/unit -q` зелёный; integration (digest_spend, миграция) зелёные; ruff чисто.
- [ ] Финальный opus broad-review: дайджест-спенд per-day корректен (не занижает); fallback-выпил не оставил prod-дыр (engine-путь цел); миграция 0024 prod-safe (chain от 0023, downgrade); dead-code удалён без битых импортов.
- [ ] Деплой: `alembic upgrade head` (0024) на боевой; рестарт digest_scheduler + затронутых воркеров.

## Границы
- `chat_id` колонку НЕ дропаем. Mini App — волна 3 (отдельно).
