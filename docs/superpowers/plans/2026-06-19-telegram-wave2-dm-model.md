# Telegram Волна 2 — DM-модель (убрать супергруппу) · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Перевести весь TG-канал на рассылку в личку всем активным recipients, убрать супергруппу/форум-топики, сделать owner-invite штатным. Закрывает 3 group-ACL бага автоматически.

**Architecture:** `alert_dispatcher` и worker-нотификации рассылают КАЖДОМУ активному recipient в личку (цикл; дедуп per-chat через существующий `UNIQUE(chat_id, ad_id, incident_key, stream_kind)`). Топики/форум-маршрутизация удаляются. Invite несёт роль (миграция), onboarding создаёт recipient с ролью из invite.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x (asyncpg), Alembic, redis.asyncio, httpx, pytest.

## Global Constraints

- Все комментарии/логи/TG-тексты — на русском; над каждым тестом короткий русский комментарий-сценарий.
- Ruff line-length=100, target py312, E/F/I/B/ASYNC; `ruff check`/`ruff format` чисто.
- НЕ pytest на боевой БД `fb_stop_bot` (:5433). Unit мокаются; integration — через изолированную `pg_engine` (fb_stop_bot_test).
- Best-effort нотификации НЕ роняют воркер.
- Money-путь: не менять FSM/owner-scoping/idempotency/дедуп-семантику алертов — только адресата (один chat_id → список recipients).
- `notify_owners` (волна 1) НЕ ломать — он остаётся для owner-only нужд; `notify_recipients` — новый параллельный.
- Дедуп per-chat: `UNIQUE` уже включает `chat_id` → N recipients = N независимых строк `telegram_message_refs`.
- Один коммит на задачу.

---

### Task 1: `load_active_recipients` + `notify_recipients`

**Files:**
- Modify: `core/telegram/service.py` (добавить `load_active_recipients`)
- Modify: `core/telegram/worker_notify.py` (добавить `notify_recipients`)
- Test: `tests/unit/test_notify_recipients.py`

**Interfaces:**
- Consumes: `load_telegram_config`, `Recipient`, `_client_for_token` (из волны 1, worker_notify).
- Produces:
  - `load_active_recipients(engine) -> list[Recipient]` — owner + recipient, `revoked_at IS NULL`.
  - `notify_recipients(engine, redis, *, category, text, dedup_key=None, dedup_ttl_seconds=None) -> bool` — рассылка ВСЕМ активным recipients в личку; dedup-after-send (общий ключ на рассылку); True если доставлено ≥1.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_notify_recipients.py
# -*- coding: utf-8 -*-
"""Unit-тесты notify_recipients: рассылка ВСЕМ активным recipients, dedup-after-send."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import core.telegram.worker_notify as wn
from core.telegram.service import Recipient


def _r(chat_id, role="recipient"):
    return Recipient(chat_id=chat_id, telegram_user_id=chat_id, username="u", role=role)


def _cfg():
    return SimpleNamespace(bot_token="T", chat_id=None)


@pytest.fixture(autouse=True)
def _clear():
    wn._reset_client_cache(); yield; wn._reset_client_cache()


# Рассылка двум recipients → 2 send, True, dedup ставится после
@pytest.mark.asyncio
async def test_broadcasts_to_all(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_active_recipients",
                        AsyncMock(return_value=[_r(111, "owner"), _r(222)]))
    client = AsyncMock()
    monkeypatch.setattr(wn, "_client_for_token", lambda t: client)
    redis = AsyncMock(); redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(object(), redis, category="x", text="t",
                                      dedup_key="k", dedup_ttl_seconds=60)
    assert sent is True
    assert client.send_message.await_count == 2
    chats = {c.kwargs["chat_id"] for c in client.send_message.await_args_list}
    assert chats == {"111", "222"}
    redis.set.assert_awaited_once()


# Нет recipients → False, без отправки и dedup
@pytest.mark.asyncio
async def test_no_recipients_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_active_recipients", AsyncMock(return_value=[]))
    redis = AsyncMock(); redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(object(), redis, category="x", text="t",
                                      dedup_key="k", dedup_ttl_seconds=60)
    assert sent is False
    redis.set.assert_not_awaited()


# Частичный сбой (один send падает) → True (доставлено ≥1), dedup ставится
@pytest.mark.asyncio
async def test_partial_failure_still_true(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_active_recipients",
                        AsyncMock(return_value=[_r(111), _r(222)]))
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=[RuntimeError("x"), {"ok": True}])
    monkeypatch.setattr(wn, "_client_for_token", lambda t: client)
    redis = AsyncMock(); redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_recipients(object(), redis, category="x", text="t",
                                      dedup_key="k", dedup_ttl_seconds=60)
    assert sent is True
    redis.set.assert_awaited_once()
```

- [ ] **Step 2: Прогон — падает**

Run: `python -m pytest tests/unit/test_notify_recipients.py -q`
Expected: FAIL — `notify_recipients`/`load_active_recipients` отсутствуют.

- [ ] **Step 3: Реализация**

`core/telegram/service.py` — после `load_owner_recipients` (волна 1):
```python
async def load_active_recipients(engine: AsyncEngine) -> list[Recipient]:
    """Все активные recipients (owner + recipient, не revoked) — адресаты DM-рассылки."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE revoked_at IS NULL
                    ORDER BY chat_id
                    """
                )
            )
        ).all()
    return [Recipient(chat_id=r[0], telegram_user_id=r[1], username=r[2], role=r[3]) for r in rows]
```
Добавить `load_active_recipients` в `__all__`.

`core/telegram/worker_notify.py` — `notify_recipients` (зеркало `notify_owners`, но `load_active_recipients`):
```python
from core.telegram.service import load_active_recipients  # к существующим импортам


async def notify_recipients(
    engine: AsyncEngine, redis: Any, *, category: str, text: str,
    dedup_key: str | None = None, dedup_ttl_seconds: int | None = None,
) -> bool:
    """Money/ops-нотификация ВСЕМ активным recipients в личку. Best-effort, dedup-after-send."""
    try:
        if dedup_key and redis is not None:
            try:
                if await redis.get(dedup_key):
                    return False
            except Exception:
                logger.exception("notify_recipients[%s]: ошибка чтения dedup %s", category, dedup_key)
        cfg = await load_telegram_config(engine)
        if cfg is None or not cfg.bot_token:
            logger.warning("notify_recipients[%s]: нет bot_token", category)
            return False
        recipients = await load_active_recipients(engine)
        if not recipients:
            logger.warning("notify_recipients[%s]: нет активных recipients", category)
            return False
        client = _client_for_token(cfg.bot_token)
        delivered = False
        for r in recipients:
            try:
                await client.send_message(chat_id=str(r.chat_id), text=text, parse_mode="HTML")
                delivered = True
            except Exception:
                logger.exception("notify_recipients[%s]: не доставлено chat_id=%s", category, r.chat_id)
        if delivered and dedup_key and redis is not None and dedup_ttl_seconds:
            try:
                await redis.set(dedup_key, "1", nx=True, ex=dedup_ttl_seconds)
            except Exception:
                logger.exception("notify_recipients[%s]: ошибка SET dedup %s", category, dedup_key)
        return delivered
    except Exception:
        logger.exception("notify_recipients[%s]: неожиданная ошибка", category)
        return False
```

- [ ] **Step 4: Прогон зелёный + ruff**

Run: `python -m pytest tests/unit/test_notify_recipients.py -q && ruff check core/telegram/service.py core/telegram/worker_notify.py tests/unit/test_notify_recipients.py`
Expected: PASS (3), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add core/telegram/service.py core/telegram/worker_notify.py tests/unit/test_notify_recipients.py
git commit -m "feat(telegram): load_active_recipients + notify_recipients (DM-рассылка всем)"
```

---

### Task 2: Миграция role в telegram_invites + owner-invite штатно

**Files:**
- Create: `migrations/versions/0023_telegram_invite_role.py`
- Modify: `core/models/telegram/invite.py` (колонка `role`)
- Modify: `core/telegram/service.py` (`find_active_invite` SELECT role)
- Modify: `core/telegram/handlers/onboarding.py` (передать `invite["role"]`)
- Modify: `scripts/create_telegram_invite.py` (писать role в колонку)
- Test: `tests/integration/test_invite_role.py`

**Interfaces:**
- `find_active_invite(engine, code) -> dict | None` — теперь с ключом `role`.
- `TelegramInvite.role: str` (default `'recipient'`).

**Контекст:** head-миграция `0022_creative_adset_meta` (`down_revision` для новой = `0022_creative_adset_meta`). Нумерация 4-значная. `consume_invite_and_create_recipient` УЖЕ принимает `role` (дефолт recipient) — менять не надо.

- [ ] **Step 1: Написать integration-тест**

```python
# tests/integration/test_invite_role.py
# -*- coding: utf-8 -*-
"""owner-invite → recipient с role='owner' (роль течёт из invite, не хардкод)."""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text
from core.telegram.service import find_active_invite, consume_invite_and_create_recipient


# invite с role='owner' → find_active_invite возвращает role; consume создаёт owner
@pytest.mark.asyncio
async def test_owner_invite_creates_owner(pg_engine):
    code = "OWNERCODE123"
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM telegram_recipients"))
        await conn.execute(text("DELETE FROM telegram_invites WHERE code=:c"), {"c": code})
        await conn.execute(text(
            "INSERT INTO telegram_invites (id, code, created_by, role, expires_at) "
            "VALUES (gen_random_uuid(), :c, 'test', 'owner', :exp)"),
            {"c": code, "exp": datetime.now(timezone.utc) + timedelta(days=1)})
    inv = await find_active_invite(pg_engine, code)
    assert inv is not None and inv["role"] == "owner"
    rec = await consume_invite_and_create_recipient(
        pg_engine, invite_id=inv["id"], chat_id=999, telegram_user_id=999,
        username="o", display_name="O", role=inv["role"])
    assert rec.role == "owner"
    async with pg_engine.connect() as conn:
        role = (await conn.execute(text(
            "SELECT role FROM telegram_recipients WHERE chat_id=999"))).scalar()
    assert role == "owner"
```

- [ ] **Step 2: Прогон — падает**

Run (изолированная БД): `python -m pytest tests/integration/test_invite_role.py -q`
Expected: FAIL — колонки `role` нет в `telegram_invites` / `find_active_invite` не возвращает role.

- [ ] **Step 3: Миграция + код**

`migrations/versions/0023_telegram_invite_role.py`:
```python
"""telegram_invites.role — роль создаваемого recipient (owner/recipient)."""
from alembic import op
import sqlalchemy as sa

revision = "0023_telegram_invite_role"
down_revision = "0022_creative_adset_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_invites",
        sa.Column("role", sa.String(16), nullable=False, server_default="recipient"),
    )
    # Backfill из метки created_by='cli:role=owner' (старые invite'ы)
    op.execute(
        "UPDATE telegram_invites SET role='owner' WHERE created_by LIKE '%role=owner%'"
    )


def downgrade() -> None:
    op.drop_column("telegram_invites", "role")
```

`core/models/telegram/invite.py` — добавить после `created_by`:
```python
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'recipient'"))
```
(импортировать `text` из sqlalchemy если ещё нет.)

`core/telegram/service.py::find_active_invite` — добавить `role` в SELECT и в возвращаемый dict:
```python
                    SELECT id, code, role, expires_at
                    FROM telegram_invites
```
```python
        return {"id": row[0], "code": row[1], "role": row[2], "expires_at": row[3]}
```

`core/telegram/handlers/onboarding.py:76` — заменить хардкод:
```python
            role=invite.get("role", "recipient"),
```

`scripts/create_telegram_invite.py` — писать role в колонку (INSERT добавить `role`):
```python
                    INSERT INTO telegram_invites (code, created_by, role, expires_at)
                    VALUES (:code, :by, :role, :exp)
```
с параметром `"role": role`.

- [ ] **Step 4: Применить миграцию на тестовой БД + прогон**

Run: `python -m pytest tests/integration/test_invite_role.py -q` (фикстура pg_engine применяет схему).
Если фикстура не гоняет alembic — проверить, что схема создаётся из моделей (модель уже с колонкой). Expected: PASS.
Затем: `ruff check migrations/versions/0023_telegram_invite_role.py core/models/telegram/invite.py core/telegram/service.py core/telegram/handlers/onboarding.py scripts/create_telegram_invite.py`

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0023_telegram_invite_role.py core/models/telegram/invite.py core/telegram/service.py core/telegram/handlers/onboarding.py scripts/create_telegram_invite.py tests/integration/test_invite_role.py
git commit -m "feat(telegram): role в telegram_invites — owner-invite штатно создаёт owner"
```

---

### Task 3: alert_dispatcher — рассылка всем recipients (MONEY-критично)

**Files:**
- Modify: `core/telegram/alert_dispatcher.py` (`dispatch_pending_alerts`, `sweep_orphan_alerts`)
- Test: `tests/integration/test_dispatch_broadcast.py`

**Interfaces:** сигнатуры `dispatch_pending_alerts`/`sweep_orphan_alerts` не меняются. Внутри — цикл по `load_active_recipients` вместо одного `config.chat_id`.

**Контекст (из разведки):** `_deliver_one_alert` УЖЕ параметризован по `chat_id` (param `:cid`). Зашивка только в `dispatch` стр.281 (`chat_id = config.chat_id`) и `sweep` стр.391. `sweep` NOT EXISTS (стр.422-427) НЕ фильтрует по chat_id — **добавить `AND r.chat_id = :cid`** (иначе при 2+ recipients sweep не дошлёт второму). `thread_id_by_stage` (стр.284-287, 392-395) из config — заменить на `{}`/None (топиков нет).

- [ ] **Step 1: Написать integration-тест**

```python
# tests/integration/test_dispatch_broadcast.py
# -*- coding: utf-8 -*-
"""dispatch рассылает алерт ВСЕМ recipients → N message_refs (per-chat дедуп)."""
from __future__ import annotations
import uuid
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from sqlalchemy import text
from core.telegram.alert_dispatcher import dispatch_pending_alerts


@pytest_asyncio.fixture
async def _seed(pg_engine):
    """2 recipient'а + fb_ad + STOP alert_event (scan_id=7)."""
    ad_id = uuid.uuid4(); tok = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in ("telegram_message_refs", "telegram_recipients", "alert_events",
                  "fb_ads", "fb_adsets", "fb_campaigns", "telegram_config"):
            await conn.execute(text(f"DELETE FROM {t}"))
        await conn.execute(text(
            "INSERT INTO telegram_config (id, bot_token_encrypted, singleton_key) "
            "VALUES (gen_random_uuid(), 'enc', 'singleton')"))
        for cid in (111, 222):
            await conn.execute(text(
                "INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), :c, :c, 'recipient')"), {"c": cid})
        cid_c = uuid.uuid4(); sid = uuid.uuid4()
        await conn.execute(text("INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) VALUES (:i,'c','CR2|KE',NOW())"), {"i": cid_c})
        await conn.execute(text("INSERT INTO fb_adsets (id, fb_adset_id, adset_name, fb_campaign_pk, last_seen_at) VALUES (:i,'s','EQ',:c,NOW())"), {"i": sid, "c": cid_c})
        await conn.execute(text("INSERT INTO fb_ads (id, fb_ad_id, ad_name, fb_adset_pk, last_seen_at) VALUES (:i,'900','Ad',:s,NOW())"), {"i": ad_id, "s": sid})
        await conn.execute(text(
            "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, metrics_json, open_state_token, scan_id, created_at) "
            "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, '{}'::jsonb, :tok, 7, NOW())"),
            {"ad": ad_id, "tok": tok})
    return {"ad_id": ad_id}


# 2 recipients → 2 message_refs, повторный dispatch не задваивает
@pytest.mark.asyncio
async def test_broadcast_two_recipients(pg_engine, _seed):
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})
    # config.chat_id остаётся NULL — раньше это был skip; теперь шлём по recipients
    res = await dispatch_pending_alerts(pg_engine, client=client, scan_id=7, redis_client=None)
    assert client.send_message.await_count == 2
    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM telegram_message_refs"))).scalar()
    assert n == 2  # по одной строке на recipient
    # повторный — дедуп per-chat, 0 новых
    client.send_message.reset_mock()
    await dispatch_pending_alerts(pg_engine, client=client, scan_id=7, redis_client=None)
    assert client.send_message.await_count == 0
```
(Колонки FK `fb_campaign_pk`/`fb_adset_pk`/`singleton_key`/`bot_token_encrypted` — сверить с актуальной схемой; поправить INSERT под реальные имена.)

- [ ] **Step 2: Прогон — падает**

Run: `python -m pytest tests/integration/test_dispatch_broadcast.py -q`
Expected: FAIL — текущий dispatch при `chat_id IS NULL` делает skip (0 send), не рассылает по recipients.

- [ ] **Step 3: Реализация**

В `dispatch_pending_alerts`: заменить блок `if config.chat_id is None: skip` + `chat_id = config.chat_id` (стр.277-281) на загрузку recipients:
```python
    config = await load_telegram_config(engine)
    if config is None or not config.bot_token:
        return {"sent": 0, "skipped_no_config": 1}
    recipients = await load_active_recipients(engine)
    if not recipients:
        logger.warning("dispatch: нет активных recipients — пропускаю")
        return {"sent": 0, "skipped_no_recipients": 1}
```
`thread_id_by_stage` (стр.284-287) → `thread_id_by_stage: dict[str, int | None] = {}` (топиков нет, всегда None).
Внешний цикл: вокруг существующего `for row in events:` обернуть `for r in recipients:` — для каждого recipient вызывать `_deliver_one_alert(..., chat_id=int(r.chat_id), thread_id_by_stage={}, ...)`. (Либо: для каждого события — внутренний цикл по recipients; выбрать порядок, не нарушив счётчики.)

В `sweep_orphan_alerts`: аналогично — цикл по recipients; в SQL NOT EXISTS добавить `AND r.chat_id = :cid` и привязать `cid=r.chat_id` per recipient (иначе sweep не дошлёт второму получателю). `thread_id_by_stage={}`.

- [ ] **Step 4: Прогон зелёный + ruff**

Run: `python -m pytest tests/integration/test_dispatch_broadcast.py tests/integration/test_alert_retry_sweep.py -q && ruff check core/telegram/alert_dispatcher.py`
Expected: PASS (retry-sweep волны 1 не сломан), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add core/telegram/alert_dispatcher.py tests/integration/test_dispatch_broadcast.py
git commit -m "feat(alert_dispatcher): рассылка алертов всем recipients (per-chat дедуп + sweep per-recipient)"
```

---

### Task 4: Перевод worker-нотификаций на recipients

**Files:**
- Modify: `apps/meta_api_worker/main.py` + `core/meta_api/autostop_alert.py` (channel-down)
- Modify: `apps/health_watchdog/main.py`
- Modify: `apps/reconciler_worker/worker.py`
- Modify: `apps/enable_recommendation_worker/main.py`
- Modify: `apps/digest_scheduler/main.py`
- Test: обновить/добавить unit на каждый (мок `notify_recipients`)

**Interfaces:** заменить `_load_tg`→chat_id+thread+send на `notify_recipients(engine, redis, ...)`.

**Контекст (из разведки):** точки send — autostop_alert.py:179-183, health/main.py:292 (`_send_alert`), reconciler/worker.py:101-105, enable/main.py:299 (`send_alert`), digest/main.py:131+200-210. У каждого `message_thread_id` = `forum_*_thread_id` (убрать). digest УЖЕ имеет `_load_active_recipients` для рассылки — оставить, убрать только доп.отправку в топик группы (стр.200-210).

- [ ] **Step 1-5 (на каждый воркер, можно одним коммитом задачи):**
  - reconciler `_maybe_alert_irreversible`: заменить тело на `await notify_recipients(engine, redis, category="reconciler_irreversible", text=render_irreversible_alert(count), dedup_key=None)` (или сохранить existing dedup). Тест: мок notify_recipients вызван.
  - health `_send_alert`: вместо прямого send — `notify_recipients` (или оставить `_maybe_alert_with_dedup`, но адресат — recipients; thread_id убрать). Минимально: `_send_alert` шлёт всем recipients. Обновить тест Task 5 волны 1 (dedup-after-send) если сигнатура меняется.
  - enable `send_alert`: адресат → recipients, `message_thread_id` убрать. Сохранить bool-контракт (волна 1 Task 6).
  - meta_api channel-down (`maybe_alert_autostop_channel_down`): сейчас шлёт в chat_id+ops-thread. Перевести на recipients (или оставить AutostopAlertContext, но адресат — все recipients; убрать thread). Сохранить dedup-семантику channel-down.
  - digest: убрать блок отправки в топик группы (main.py:200-210); `_load_active_recipients` рассылка остаётся (thread_id уже None).
  - Каждый — TDD: тест проверяет, что нотификация идёт через recipients-путь, thread_id не используется. Прогон unit каждого воркера + ruff. Commit.

(Детализация по каждому воркеру — реализатор сверяет точные строки из разведки выше; паттерн идентичен: убрать chat_id+forum_thread, звать notify_recipients.)

```bash
git commit -m "feat(workers): worker-нотификации через notify_recipients (убрать forum-топики)"
```

---

### Task 5: Удалить супергруппу/форум-топики

**Files:**
- Delete: `core/telegram/topics.py`, `core/telegram/handlers/topics.py`
- Modify: `core/telegram/handlers/router.py` (убрать import + setup_topics/topics команды)
- Modify: `apps/api/routers/v1/settings_telegram.py` (убрать `post_setup_topics` endpoint)
- Modify: `core/telegram/service.py` (TelegramConfig dataclass: убрать `forum_*_thread_id` поля + SELECT + переиндексация конструктора)
- Modify: `core/models/settings/telegram_config.py` (убрать `forum_*_thread_id` mapped_column'ы — колонки в БД остаются мёртвыми до волны 4)
- Modify: `core/telegram/handlers/onboarding.py` (убрать секцию топиков из /help, стр.139-141)
- Modify: `core/telegram/alert_dispatcher.py` (убрать `forum_*` из `thread_id_by_stage`, `_send_alert_with_fallback` thread-fallback — если ещё остался после Task 3)
- Test: обновить тесты, ссылающиеся на топики/forum-поля; `tests/unit/test_tg_acl_guards.py` (setup_topics/topics → удалить кейсы)

**Контекст (из разведки):** topics.py импортируется только handlers/topics.py:22 + settings_telegram.py:367. router.py: import стр.37, `_OWNER_ONLY` стр.70-71, блоки стр.374-383/385-393. `message_thread_id` из `msg["message_thread_id"]` (reply в источник) НЕ трогать — только из `forum_*` конфига.

- [ ] Шаги: удалить файлы → убрать импорты/регистрации → убрать forum-поля из dataclass/ORM/SELECT (переиндексировать `row[N]`) → обновить тесты → `ruff` + полный unit-прогон (убедиться, что удаление не сломало импорты) → commit.

```bash
git rm core/telegram/topics.py core/telegram/handlers/topics.py
git commit -m "refactor(telegram): удалить супергруппу/форум-топики (DM-модель)"
```

---

### Task 6: group-ACL cleanup

**Files:**
- Modify: `core/telegram/handlers/router.py` (убрать `_is_private`-гейт-дыру)
- Test: `tests/unit/test_tg_acl_guards.py` (обновить: незарегистрированный в любом чате → отказ)

**Контекст:** аудит group-auth-bypass — `if not recipient and _is_private(chat_type)` срабатывает только в личке; в группе незарегистрированный проходит. После удаления группы бот в личке, но гейт надо сделать безусловным.

- [ ] Шаги: заменить гейт на `if not recipient: <отказ>; return` для всех команд кроме `/start`, независимо от типа чата → тест (recipient=None → отказ) → ruff → commit.

```bash
git commit -m "fix(telegram): безусловный ACL-гейт (закрыть group-auth-bypass)"
```

---

### Task 7: Финальная верификация + broad-review

- [ ] `python -m pytest tests/unit -q` зелёный; integration (invite_role, dispatch_broadcast, retry_sweep) на изолированной БД зелёные; ruff чисто.
- [ ] Финальный broad whole-branch review (opus) — money-фокус: рассылка не задваивает/не теряет алерты, per-chat дедуп корректен, sweep per-recipient, owner-invite штатно, топики не оставили битых импортов, форум-колонки мёртвые (не используются), group-ACL закрыт.
- [ ] Применить миграцию `0023` на боевой БД (`alembic upgrade head`) при деплое; рестарт воркеров.
- [ ] Живая проверка: STOP-алерт приходит всем активным recipients в личку с рабочими кнопками.

## Замечания
- Форум-колонки `forum_*_thread_id` + `chat_id` остаются в БД мёртвыми; DROP — волна 4.
- `notify_owners` (волна 1) остаётся для owner-only (если понадобится); по умолчанию волна 2 — всем через `notify_recipients`.
