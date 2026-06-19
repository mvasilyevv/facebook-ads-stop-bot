# Telegram Волна 1 — money-надёжность нотификаций · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрыть класс money-провалов, которые сейчас молча уходят в лог — провал авто-стопа/паузы, тихий sync OFF→disabled, провал автостарта, потеря алерта при TG-outage, dedup-before-send.

**Architecture:** Единый модуль `core/telegram/worker_notify.py::notify_owners` — переиспользуемая best-effort точка нотификации воркеров (свежий токен, кеш клиента по токену, dedup ТОЛЬКО после успешной отправки, возврат bool). Адресат волны 1 — owner-recipient'ы в личку (DM-фундамент целевого формата). Существующие пути (channel-down алерт в meta_api, health, enable, dispatcher) фиксятся точечно. mark_disabled-нотификация прокидывается из pipeline через `CycleResult` в observer_worker (где доступен redis).

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x (asyncpg), redis.asyncio, httpx (Telegram Bot API), pytest/pytest-asyncio.

## Global Constraints

- Все комментарии, лог-сообщения и TG-тексты — на русском (CLAUDE.md).
- Над каждым тестом — короткий русский комментарий-сценарий (CLAUDE.md).
- Ruff: line-length=100, target py312, правила E/F/I/B/ASYNC. `ruff check .` и `ruff format .` чисто.
- НЕ запускать pytest на боевой БД :5433 (integration сносит offers/telegram_config). Unit — можно везде. Integration-тесты добавлять, но прогон — в изолированной БД.
- Best-effort нотификации НЕ должны ронять воркер: любые исключения TG/Redis ловятся и логируются.
- `_AUTO_STOP_MAX_ATTEMPTS = 15` (~1ч) — НЕ трогать (осознанное решение владельца).
- Money-путь: не менять FSM-переходы, owner-scoping, idempotency_key — только добавлять нотификации и точечные фиксы доставки.
- Frequent commits: один коммит на задачу (после прохождения тестов).

---

### Task 1: Модуль `worker_notify` + выборка owner-recipients

**Files:**
- Modify: `core/telegram/service.py` (добавить `load_owner_recipients`)
- Create: `core/telegram/worker_notify.py`
- Test: `tests/unit/test_worker_notify.py`

**Interfaces:**
- Consumes: `load_telegram_config(engine) -> TelegramConfig | None` (поле `.bot_token: str`), `Recipient(chat_id:int, telegram_user_id:int, username:str|None, role:str)`, `TelegramBotClient(bot_token).send_message(chat_id:str, text:str, message_thread_id:int|None=None, reply_markup:dict|None=None, parse_mode:str|None="HTML")`.
- Produces:
  - `load_owner_recipients(engine) -> list[Recipient]` — активные `role='owner'`.
  - `notify_owners(engine, redis, *, category:str, text:str, dedup_key:str|None=None, dedup_ttl_seconds:int|None=None) -> bool` — True если доставлено ≥1 owner.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_worker_notify.py
# -*- coding: utf-8 -*-
"""Unit-тесты worker_notify: best-effort DM owner'ам с dedup ПОСЛЕ отправки."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import core.telegram.worker_notify as wn
from core.telegram.service import Recipient


def _owner(chat_id=111):
    return Recipient(chat_id=chat_id, telegram_user_id=1, username="u", role="owner")


def _cfg():
    return SimpleNamespace(bot_token="T", chat_id=None)


@pytest.fixture(autouse=True)
def _clear_client_cache():
    wn._reset_client_cache()
    yield
    wn._reset_client_cache()


# Нет owner-получателей → no-op, возвращает False, dedup не ставится
@pytest.mark.asyncio
async def test_no_recipients_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[]))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(object(), redis, category="x", text="t",
                                  dedup_key="k", dedup_ttl_seconds=60)
    assert sent is False
    redis.set.assert_not_awaited()


# Успех доставки → True, dedup ставится ПОСЛЕ отправки (SET с nx+ex)
@pytest.mark.asyncio
async def test_success_sets_dedup_after_send(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[_owner()]))
    client = AsyncMock()
    monkeypatch.setattr(wn, "_client_for_token", lambda tok: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(object(), redis, category="x", text="t",
                                  dedup_key="k", dedup_ttl_seconds=60)
    assert sent is True
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["chat_id"] == "111"
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("nx") is True
    assert redis.set.await_args.kwargs.get("ex") == 60


# Отправка упала → dedup НЕ ставится (чтобы ретрайнуть позже), возвращает False
@pytest.mark.asyncio
async def test_send_failure_keeps_dedup_unset(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    monkeypatch.setattr(wn, "load_owner_recipients", AsyncMock(return_value=[_owner()]))
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=RuntimeError("tg down"))
    monkeypatch.setattr(wn, "_client_for_token", lambda tok: client)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(object(), redis, category="x", text="t",
                                  dedup_key="k", dedup_ttl_seconds=60)
    assert sent is False
    redis.set.assert_not_awaited()


# dedup уже стоит → ранний выход, отправки нет
@pytest.mark.asyncio
async def test_dedup_already_set_skips(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=_cfg()))
    lor = AsyncMock(return_value=[_owner()])
    monkeypatch.setattr(wn, "load_owner_recipients", lor)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1")
    sent = await wn.notify_owners(object(), redis, category="x", text="t",
                                  dedup_key="k", dedup_ttl_seconds=60)
    assert sent is False
    lor.assert_not_awaited()


# Нет токена в конфиге → no-op False (не падает)
@pytest.mark.asyncio
async def test_no_token_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "load_telegram_config", AsyncMock(return_value=None))
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    sent = await wn.notify_owners(object(), redis, category="x", text="t")
    assert sent is False
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `python -m pytest tests/unit/test_worker_notify.py -q`
Expected: FAIL — `ModuleNotFoundError: core.telegram.worker_notify` / `load_owner_recipients` отсутствует.

- [ ] **Step 3: Реализовать `load_owner_recipients` в `core/telegram/service.py`**

Добавить после `find_recipient_by_telegram_user_id` (рядом с другими выборками). Использовать те же колонки, что `find_recipient` (`chat_id, telegram_user_id, username, role`):

```python
async def load_owner_recipients(engine: AsyncEngine) -> list[Recipient]:
    """Все активные owner-recipient'ы (role='owner', не revoked) — адресаты DM-нотификаций.

    Возвращает список (может быть пустым). chat_id — private chat из /start.
    """
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT chat_id, telegram_user_id, username, role
                    FROM telegram_recipients
                    WHERE role = 'owner' AND revoked_at IS NULL
                    ORDER BY chat_id
                    """
                )
            )
        ).all()
    return [
        Recipient(chat_id=r[0], telegram_user_id=r[1], username=r[2], role=r[3])
        for r in rows
    ]
```

Добавить `load_owner_recipients` в `__all__` модуля service.py (если он есть).

- [ ] **Step 4: Реализовать `core/telegram/worker_notify.py`**

```python
# -*- coding: utf-8 -*-
"""Единая best-effort точка money-нотификаций воркеров в Telegram.

Зачем: meta_api_worker/cabinet_scheduler и др. при провале денежных операций
писали только в лог. notify_owners шлёт owner-recipient'ам в ЛИЧКУ (DM-формат),
с dedup ТОЛЬКО после успешной доставки (чтобы сбой TG не «съел» алерт на TTL).

Best-effort: исключения TG/Redis ловятся и логируются — воркер не падает.
Клиент кешируется по bot_token (свежий токен подхватывается при ротации).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.client import TelegramBotClient
from core.telegram.service import load_owner_recipients, load_telegram_config

logger = logging.getLogger(__name__)

# Кеш клиента по токену: при ротации токена создаётся новый, старый отбрасывается.
_client_cache: dict[str, TelegramBotClient] = {}


def _client_for_token(bot_token: str) -> TelegramBotClient:
    client = _client_cache.get(bot_token)
    if client is None:
        client = TelegramBotClient(bot_token)
        _client_cache.clear()  # держим один токен (на ротации старый не нужен)
        _client_cache[bot_token] = client
    return client


def _reset_client_cache() -> None:
    """Только для тестов."""
    _client_cache.clear()


async def notify_owners(
    engine: AsyncEngine,
    redis: Any,
    *,
    category: str,
    text: str,
    dedup_key: str | None = None,
    dedup_ttl_seconds: int | None = None,
) -> bool:
    """Отправить money-нотификацию всем owner-recipient'ам в личку.

    Returns: True если доставлено хотя бы одному owner. Best-effort — не бросает.
    dedup_key (если задан) ставится в Redis SET NX EX ТОЛЬКО после успешной доставки.
    """
    try:
        if dedup_key and redis is not None:
            try:
                if await redis.get(dedup_key):
                    return False
            except Exception:
                logger.exception("worker_notify[%s]: ошибка чтения dedup %s", category, dedup_key)

        cfg = await load_telegram_config(engine)
        if cfg is None or not cfg.bot_token:
            logger.warning("worker_notify[%s]: нет bot_token — пропускаю", category)
            return False

        owners = await load_owner_recipients(engine)
        if not owners:
            logger.warning("worker_notify[%s]: нет owner-получателей — пропускаю", category)
            return False

        client = _client_for_token(cfg.bot_token)
        delivered = False
        for owner in owners:
            try:
                await client.send_message(
                    chat_id=str(owner.chat_id), text=text, parse_mode="HTML"
                )
                delivered = True
            except Exception:
                logger.exception(
                    "worker_notify[%s]: не доставлено owner chat_id=%s", category, owner.chat_id
                )

        if delivered and dedup_key and redis is not None and dedup_ttl_seconds:
            try:
                await redis.set(dedup_key, "1", nx=True, ex=dedup_ttl_seconds)
            except Exception:
                logger.exception("worker_notify[%s]: ошибка SET dedup %s", category, dedup_key)
        return delivered
    except Exception:
        logger.exception("worker_notify[%s]: неожиданная ошибка", category)
        return False
```

- [ ] **Step 5: Прогнать тесты — зелёные + ruff**

Run: `python -m pytest tests/unit/test_worker_notify.py -q && ruff check core/telegram/worker_notify.py core/telegram/service.py tests/unit/test_worker_notify.py`
Expected: PASS (5 passed), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add core/telegram/worker_notify.py core/telegram/service.py tests/unit/test_worker_notify.py
git commit -m "feat(telegram): worker_notify — единая DM-нотификация owner'ам с dedup-after-send"
```

---

### Task 2: meta_api_worker — финальный провал money-мутаций → TG

**Files:**
- Modify: `apps/meta_api_worker/main.py` (ветки `mark_task_failed`: partial-create, permanent, exhausted; token-invalid)
- Test: `tests/unit/test_meta_worker_fail_alert.py`

**Interfaces:**
- Consumes: `notify_owners(engine, redis, *, category, text, dedup_key, dedup_ttl_seconds)`, `task.requested_by`, `payload.mutation_kind`, `payload.target_id`, `CreateCampaignPartialError.created_ids`.
- Produces: helper `_alert_money_fail(engine, redis, *, payload, requested_by, error, kind_label) -> None` в main.py.

**Контекст (из кода):** `process_one_task(engine, task, *, client, redis_client, alert_ctx)`. Ветки: partial (406-428), permanent (429-439), temporary (440-469, тут уже есть channel-down алерт — НЕ трогаем), value_error (470-492). Финальный провал = `mark_task_failed`. `_PAUSE_KINDS = {"pause_ad", "bulk_status_change"}` — money-стоп.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_meta_worker_fail_alert.py
# -*- coding: utf-8 -*-
"""Финальный провал money-мутации (pause/permanent/partial) шлёт TG owner'ам."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import apps.meta_api_worker.main as mw
from core.meta_api.schemas import MetaMutationPayload


def _payload(kind="pause_ad", target="12345"):
    return MetaMutationPayload(mutation_kind=kind, target_id=target, params={})


# Провал pause_ad (auto-stop) → notify_owners с money-текстом и dedup auto_stop_fail
@pytest.mark.asyncio
async def test_pause_fail_alerts_owner(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(), AsyncMock(),
        payload=_payload(), requested_by="bot_auto_stop",
        error="PermanentError(code=368)", kind_label="pause_ad",
    )
    spy.assert_awaited_once()
    kw = spy.await_args.kwargs
    assert "12345" in kw["text"]
    assert kw["dedup_key"] == "auto_stop_fail:12345"
    assert kw["dedup_ttl_seconds"] == 3600


# Не-money-мутация (set_adset_budget) → НЕ алертим (не money-стоп)
@pytest.mark.asyncio
async def test_non_money_kind_no_alert(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(mw, "notify_owners", spy)
    await mw._alert_money_fail(
        object(), AsyncMock(),
        payload=_payload(kind="set_adset_budget"), requested_by="user",
        error="x", kind_label="set_adset_budget",
    )
    spy.assert_not_awaited()
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `python -m pytest tests/unit/test_meta_worker_fail_alert.py -q`
Expected: FAIL — `_alert_money_fail` отсутствует.

- [ ] **Step 3: Реализовать `_alert_money_fail` + вызовы**

В `apps/meta_api_worker/main.py` добавить рядом с импортами:
```python
from core.telegram.worker_notify import notify_owners

_PAUSE_KINDS = frozenset({"pause_ad", "bulk_status_change"})
```

Добавить helper:
```python
async def _alert_money_fail(
    engine,
    redis_client,
    *,
    payload,
    requested_by: str,
    error: str,
    kind_label: str,
) -> None:
    """Финальный провал money-мутации (пауза/permanent) → DM owner'ам. Best-effort.

    Алертим только денежные действия: pause/bulk (стоп рекламы). Бюджет/прочее — нет.
    """
    if payload.mutation_kind not in _PAUSE_KINDS:
        return
    actor = "Авто-стоп" if requested_by == _AUTO_STOP_REQUESTED_BY else "Пауза"
    text = (
        f"❌ <b>{actor} не сработал окончательно</b>\n"
        f"fb_ad_id=<code>{payload.target_id}</code> ({kind_label})\n"
        f"Ошибка: {error[:200]}\n"
        f"Отключи объявление вручную."
    )
    await notify_owners(
        engine, redis_client,
        category="money_fail",
        text=text,
        dedup_key=f"auto_stop_fail:{payload.target_id}",
        dedup_ttl_seconds=3600,
    )
```

Вставить вызов после `mark_task_failed` в 3 ветках (partial 406-428, permanent 429-439, exhausted внутри 440-469 при исчерпании). В каждой, где есть `payload` и `engine`/`redis_client`:
```python
        await _alert_money_fail(
            engine, redis_client,
            payload=payload, requested_by=getattr(task, "requested_by", ""),
            error=str(exc), kind_label=payload.mutation_kind,
        )
```
Для permanent-ветки (TokenInvalidError входит в `_PERMANENT_EXCEPTIONS`) — тот же вызов; для exhausted retries (внутри temporary, где `requeue_task` вернул финальный fail) — добавить вызов рядом с `logger.error("exhausted retries")`.

Дополнительно: TokenInvalidError → отдельный дедуплицированный алерт «токен истёк, re-login Vision» (category=`token_invalid`, dedup_key=`meta_token_invalid`, TTL 3600) — добавить проверку `isinstance(exc, TokenInvalidError)` в permanent-ветке перед money-fail.

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_meta_worker_fail_alert.py -q && ruff check apps/meta_api_worker/main.py tests/unit/test_meta_worker_fail_alert.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add apps/meta_api_worker/main.py tests/unit/test_meta_worker_fail_alert.py
git commit -m "feat(meta_api_worker): TG owner'у при финальном провале money-мутации (CR008-класс)"
```

---

### Task 3: observer sync OFF→disabled → TG (через CycleResult)

**Files:**
- Modify: `core/observer/pipeline.py` (добавить поле в `CycleResult` + заполнять в sync-ветке)
- Modify: `apps/observer_worker/main.py` (после `process_scan_rows` — notify по списку)
- Test: `tests/unit/test_sync_disabled_alert.py`

**Interfaces:**
- Consumes: `CycleResult`, `notify_owners`, `row.fb_ad_id`.
- Produces: `CycleResult.synced_offline_disabled: list[str]` (fb_ad_id, default `field(default_factory=list)`).

**Контекст:** redis НЕ доступен в `_process_one_row`, но доступен в observer_worker. Поэтому pipeline только собирает список, observer_worker шлёт (паттерн как alerts).

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_sync_disabled_alert.py
# -*- coding: utf-8 -*-
"""observer_worker шлёт DM при sync OFF→disabled по списку из CycleResult."""
from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
import apps.observer_worker.main as ow


# Для каждого synced_offline_disabled — notify_owners с dedup sync_offline_disabled:{id}
@pytest.mark.asyncio
async def test_notifies_for_synced_disabled(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(ow, "notify_owners", spy)
    await ow._notify_synced_disabled(
        object(), AsyncMock(), fb_ad_ids=["100", "200"]
    )
    assert spy.await_count == 2
    keys = {c.kwargs["dedup_key"] for c in spy.await_args_list}
    assert keys == {"sync_offline_disabled:100", "sync_offline_disabled:200"}
    assert spy.await_args_list[0].kwargs["dedup_ttl_seconds"] == 21600


# Пустой список → ничего не шлём
@pytest.mark.asyncio
async def test_empty_no_notify(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ow, "notify_owners", spy)
    await ow._notify_synced_disabled(object(), AsyncMock(), fb_ad_ids=[])
    spy.assert_not_awaited()
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/unit/test_sync_disabled_alert.py -q`
Expected: FAIL — `_notify_synced_disabled` отсутствует.

- [ ] **Step 3: Реализация**

В `core/observer/pipeline.py` — в dataclass `CycleResult` добавить:
```python
    synced_offline_disabled: list[str] = field(default_factory=list)
```
(проверить, что `from dataclasses import field` импортирован.)

В sync-ветке (строки 354-361) перед `return` добавить:
```python
        result.synced_offline_disabled.append(row.fb_ad_id)
```

В `apps/observer_worker/main.py` — импорт + helper + вызов:
```python
from core.telegram.worker_notify import notify_owners


async def _notify_synced_disabled(engine, redis_client, *, fb_ad_ids: list[str]) -> None:
    """DM owner'у про тихий sync OFF→disabled (внешнее отключение ада). Best-effort."""
    for fb_ad_id in fb_ad_ids:
        text = (
            f"ℹ️ <b>Объявление помечено disabled</b>\n"
            f"fb_ad_id=<code>{fb_ad_id}</code> — в Meta уже OFF "
            f"(внешнее отключение/наш pause не подтвердился)."
        )
        await notify_owners(
            engine, redis_client,
            category="sync_disabled", text=text,
            dedup_key=f"sync_offline_disabled:{fb_ad_id}",
            dedup_ttl_seconds=21600,
        )
```
После вызова `process_scan_rows(...)` в `_run_account_scan` (рядом с `dispatch_pending_alerts`):
```python
    if cycle_result.synced_offline_disabled:
        await _notify_synced_disabled(
            engine, redis_client, fb_ad_ids=cycle_result.synced_offline_disabled
        )
```

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_sync_disabled_alert.py tests/unit/test_observer_state_machine.py -q && ruff check core/observer/pipeline.py apps/observer_worker/main.py tests/unit/test_sync_disabled_alert.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add core/observer/pipeline.py apps/observer_worker/main.py tests/unit/test_sync_disabled_alert.py
git commit -m "feat(observer): DM owner'у при sync OFF→disabled (раньше тихо)"
```

---

### Task 4: cabinet_scheduler — автостарт started/no_owner_ads → TG + фикс done-маркера

**Files:**
- Modify: `apps/cabinet_scheduler/main.py` (после `run_one_tick`-резолва + перенос done-маркера)
- Test: `tests/unit/test_cabinet_autostart_alert.py`

**Interfaces:**
- Consumes: `notify_owners`, summary `run_one_tick` (поля `outcome`, `day`, `ad_count`, `task_id`).
- Produces: helper `_alert_autostart(engine, redis_client, summary: dict) -> None`.

**Контекст:** `_set_autostart_done` (204-209) сейчас вызывается ДО возврата `no_owner_ads` (строка 189). Нужно: маркер ставить только при `started`/успехе, чтобы `no_owner_ads` ретраился в окне.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_cabinet_autostart_alert.py
# -*- coding: utf-8 -*-
"""Автостарт: started → подтверждение, no_owner_ads → алерт; оба best-effort."""
from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
import apps.cabinet_scheduler.main as cab


# started → notify с числом объявлений
@pytest.mark.asyncio
async def test_started_confirms(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(object(), AsyncMock(),
                               {"outcome": "started", "day": "2026-06-20",
                                "ad_count": 7, "task_id": 42})
    spy.assert_awaited_once()
    assert "7" in spy.await_args.kwargs["text"]


# no_owner_ads → алерт «кабинет не поднят»
@pytest.mark.asyncio
async def test_no_owner_ads_alerts(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(object(), AsyncMock(),
                               {"outcome": "no_owner_ads", "day": "2026-06-20"})
    spy.assert_awaited_once()
    assert "не поднят" in spy.await_args.kwargs["text"].lower() or \
           "не найдено" in spy.await_args.kwargs["text"].lower()


# прочие outcome (already_done/scanning_paused) → молчим
@pytest.mark.asyncio
async def test_other_outcome_silent(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(cab, "notify_owners", spy)
    await cab._alert_autostart(object(), AsyncMock(), {"outcome": "already_done"})
    spy.assert_not_awaited()
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/unit/test_cabinet_autostart_alert.py -q`
Expected: FAIL — `_alert_autostart` отсутствует.

- [ ] **Step 3: Реализация**

В `apps/cabinet_scheduler/main.py` — импорт + helper:
```python
from core.telegram.worker_notify import notify_owners


async def _alert_autostart(engine, redis_client, summary: dict) -> None:
    """Подтверждение/алерт автостарта кабинета. Best-effort, дедуп по дню."""
    outcome = summary.get("outcome")
    day = summary.get("day", "")
    if outcome == "started":
        text = (
            f"🚀 <b>Автостарт кабинета {day}</b>\n"
            f"Поставлено объявлений: {summary.get('ad_count')} "
            f"(task_id={summary.get('task_id')})."
        )
    elif outcome == "no_owner_ads":
        text = (
            f"⚠️ <b>Автостарт {day}: owner-объявлений не найдено</b>\n"
            f"Кабинет НЕ поднят. Проверь даты в названиях кампаний."
        )
    else:
        return
    await notify_owners(
        engine, redis_client, category="autostart", text=text,
        dedup_key=f"autostart_alert:{day}:{outcome}", dedup_ttl_seconds=93600,
    )
```
Перенести `_set_autostart_done`: убрать его вызов из пути, ведущего к `no_owner_ads` (строка ~189), оставить только в `started`-пути (после успешного создания задачи). В месте, где `run_one_tick` возвращает summary (в вызывающем main-loop), добавить:
```python
    summary = await run_one_tick(engine=engine, redis_client=redis_client, now=now)
    await _alert_autostart(engine, redis_client, summary)
```

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_cabinet_autostart_alert.py tests/unit/test_asymmetric_stop.py -q && ruff check apps/cabinet_scheduler/main.py tests/unit/test_cabinet_autostart_alert.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add apps/cabinet_scheduler/main.py tests/unit/test_cabinet_autostart_alert.py
git commit -m "feat(cabinet_scheduler): TG-подтверждение/алерт автостарта + done-маркер после резолва"
```

---

### Task 5: health_watchdog — dedup ТОЛЬКО после успешной отправки

**Files:**
- Modify: `apps/health_watchdog/main.py` (`_send_alert` → bool, `_maybe_alert_with_dedup` порядок)
- Test: `tests/unit/test_health_dedup_after_send.py`

**Interfaces:**
- Consumes/Produces: `_send_alert(...) -> bool` (был `None`); `_maybe_alert_with_dedup` ставит SET NX только при `sent is True`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_health_dedup_after_send.py
# -*- coding: utf-8 -*-
"""health_watchdog: при сбое TG dedup-ключ НЕ ставится (алерт не теряется на TTL)."""
from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
import apps.health_watchdog.main as hw


# Отправка упала → SET NX не вызывается, возвращает False
@pytest.mark.asyncio
async def test_send_fail_no_dedup(monkeypatch):
    monkeypatch.setattr(hw, "_send_alert", AsyncMock(return_value=False))
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    ok = await hw._maybe_alert_with_dedup(
        redis, dedup_key="k", text="t", tg_client=object(), chat_id="1", thread_id=None
    )
    assert ok is False
    redis.set.assert_not_awaited()


# Отправка ок → SET NX ставится, возвращает True
@pytest.mark.asyncio
async def test_send_ok_sets_dedup(monkeypatch):
    monkeypatch.setattr(hw, "_send_alert", AsyncMock(return_value=True))
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    ok = await hw._maybe_alert_with_dedup(
        redis, dedup_key="k", text="t", tg_client=object(), chat_id="1", thread_id=None
    )
    assert ok is True
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("nx") is True
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/unit/test_health_dedup_after_send.py -q`
Expected: FAIL — текущий `_maybe_alert_with_dedup` ставит SET до отправки (порядок) и `_send_alert` возвращает None.

- [ ] **Step 3: Реализация**

`_send_alert` — вернуть bool успеха:
```python
async def _send_alert(tg_client, *, chat_id, thread_id, text) -> bool:
    logger.warning("ALERT: %s", text)
    if tg_client is None or not chat_id:
        return False
    try:
        await tg_client.send_message(chat_id=chat_id, text=text,
                                     message_thread_id=thread_id, parse_mode="HTML")
        return True
    except TelegramAPIError as exc:
        logger.error("не удалось отправить TG-алерт: %s", exc)
        return False
    except Exception:
        logger.exception("неожиданная ошибка при отправке TG-алерта")
        return False
```
`_maybe_alert_with_dedup` — сначала отправка, SET NX только при успехе:
```python
async def _maybe_alert_with_dedup(redis_client, *, dedup_key, text, tg_client, chat_id, thread_id) -> bool:
    # дедуп-проверка: уже алертили в окне?
    try:
        if await redis_client.get(dedup_key):
            return False
    except Exception:
        logger.exception("ошибка чтения дедуп-ключа %s", dedup_key)
    sent = await _send_alert(tg_client, chat_id=chat_id, thread_id=thread_id, text=text)
    if not sent:
        return False
    try:
        await redis_client.set(dedup_key, "1", ex=ALERT_DEDUP_TTL_SECONDS, nx=True)
    except Exception:
        logger.exception("ошибка SET дедуп-ключа %s", dedup_key)
    return True
```

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_health_dedup_after_send.py tests/unit/test_health_watchdog.py -q && ruff check apps/health_watchdog/main.py tests/unit/test_health_dedup_after_send.py`
Expected: PASS, ruff clean. (Если `test_health_watchdog.py` ассертил SET-before-send — обновить под новый контракт.)

- [ ] **Step 5: Commit**

```bash
git add apps/health_watchdog/main.py tests/unit/test_health_dedup_after_send.py
git commit -m "fix(health_watchdog): dedup-ключ только после успешной отправки (не терять алерт на TTL)"
```

---

### Task 6: enable_reco — mark_recommended после успешной отправки

**Files:**
- Modify: `apps/enable_recommendation_worker/main.py` (порядок `send_alert`/`mark_recommended`, `send_alert -> bool`)
- Test: `tests/unit/test_enable_reco_order.py`

**Interfaces:** `send_alert(...) -> bool`; `mark_recommended` вызывается ТОЛЬКО при успешной отправке.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_enable_reco_order.py
# -*- coding: utf-8 -*-
"""enable_reco: при сбое TG mark_recommended НЕ ставится (рекомендация не теряется)."""
from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
import apps.enable_recommendation_worker.main as er


# send_alert возвращает bool успеха
@pytest.mark.asyncio
async def test_send_alert_returns_bool(monkeypatch):
    client = AsyncMock()
    res = await er.send_alert(client, chat_id="1", thread_id=None,
                              candidate=_fake_candidate(), decision=_fake_decision())
    assert res is True


# send_alert упал → возвращает False (мок client кидает)
@pytest.mark.asyncio
async def test_send_alert_failure_false(monkeypatch):
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=RuntimeError("down"))
    res = await er.send_alert(client, chat_id="1", thread_id=None,
                              candidate=_fake_candidate(), decision=_fake_decision())
    assert res is False
```
(вспомогательные `_fake_candidate`/`_fake_decision` — минимальные SimpleNamespace под текущую `send_alert`; подсмотреть поля в `CandidateRow`/`RecommendationDecision`.)

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/unit/test_enable_reco_order.py -q`
Expected: FAIL — `send_alert` возвращает None.

- [ ] **Step 3: Реализация**

`send_alert` — вернуть bool (True при успешной отправке, False при исключении/нет chat_id). В `run_once` (378-406) изменить порядок: сначала `insert_recommendation` (нужен для idempotency), затем `send_alert`, и `mark_recommended` ТОЛЬКО если `await send_alert(...)` вернул True:
```python
        new_id = await insert_recommendation(...)
        if new_id is None:
            counts["skipped_decision"] += 1
            continue
        sent = await send_alert(tg_client, chat_id=chat_id, thread_id=thread_id,
                                candidate=cand, decision=decision)
        if not sent:
            counts["send_failed"] += 1
            continue
        await mark_recommended(redis_client, cand.ad_id)
        counts["alerts_sent"] += 1
```

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_enable_reco_order.py -q && ruff check apps/enable_recommendation_worker/main.py tests/unit/test_enable_reco_order.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add apps/enable_recommendation_worker/main.py tests/unit/test_enable_reco_order.py
git commit -m "fix(enable_reco): mark_recommended после успешной отправки (не терять рекомендацию)"
```

---

### Task 7: alert_dispatcher — retry-sweep осиротевших + redis_client проброс

**Files:**
- Modify: `core/telegram/alert_dispatcher.py` (новая `sweep_orphan_alerts`, вызов внутри `dispatch_pending_alerts` или отдельно)
- Modify: `apps/observer_worker/main.py` (передать `redis_client`; вызывать sweep каждый цикл)
- Test: `tests/integration/test_alert_retry_sweep.py` (integration; НЕ прогонять на боевой БД)

**Interfaces:**
- Produces: `sweep_orphan_alerts(engine, *, client, redis_client=None, hours=24) -> dict[str,int]` — ресенд `alert_events` без `telegram_message_refs`.
- Modify call: `dispatch_pending_alerts(engine, client=tg_client, scan_id=scan_id, redis_client=redis_client)`.

**Контекст:** `alert_events` (partitioned по `created_at`), сопоставление с `telegram_message_refs`: `ad_id` + `incident_key=open_state_token::text` + `stream_kind=stage`. Осиротевший = есть event, нет ref (или ref только sentinel удалён).

- [ ] **Step 1: Написать integration-тест**

```python
# tests/integration/test_alert_retry_sweep.py
# -*- coding: utf-8 -*-
"""retry-sweep ресендит alert_event без message_ref и не трогает уже доставленный."""
from __future__ import annotations
import uuid
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from sqlalchemy import text
from core.telegram.alert_dispatcher import sweep_orphan_alerts


@pytest_asyncio.fixture
async def _seed_orphan(pg_engine):
    """Один fb_ad + STOP alert_event БЕЗ message_ref (осиротевший, в 24h окне)."""
    ad_id = uuid.uuid4()
    token = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in ("telegram_message_refs", "alert_events", "fb_ads",
                  "fb_adsets", "fb_campaigns"):
            await conn.execute(text(f"DELETE FROM {t}"))
        cid = uuid.uuid4(); sid = uuid.uuid4()
        await conn.execute(text(
            "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
            "VALUES (:i,'c1','CR2 | KE', NOW())"), {"i": cid})
        await conn.execute(text(
            "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, fb_campaign_pk, last_seen_at) "
            "VALUES (:i,'s1','EQ', :c, NOW())"), {"i": sid, "c": cid})
        await conn.execute(text(
            "INSERT INTO fb_ads (id, fb_ad_id, ad_name, fb_adset_pk, last_seen_at) "
            "VALUES (:i,'900','Ad', :s, NOW())"), {"i": ad_id, "s": sid})
        await conn.execute(text(
            "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
            "metrics_json, open_state_token, scan_id, created_at) "
            "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
            "'{}'::jsonb, :tok, 1, NOW())"), {"ad": ad_id, "tok": token})
    return {"ad_id": ad_id, "token": token}


# Осиротевший event ресендится; message_ref создан; повторный sweep → 0
@pytest.mark.asyncio
async def test_sweep_resends_orphan(pg_engine, _seed_orphan):
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"result": {"message_id": 555}})
    res = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert res["sent"] == 1
    client.send_message.assert_awaited_once()
    async with pg_engine.connect() as conn:
        n = (await conn.execute(text(
            "SELECT count(*) FROM telegram_message_refs WHERE message_id = 555"))).scalar()
    assert n == 1
    # второй прогон — уже доставлено, ресенда нет
    res2 = await sweep_orphan_alerts(pg_engine, client=client, redis_client=None, hours=24)
    assert res2["sent"] == 0
```
(Имена FK-колонок `fb_adset_pk`/`fb_campaign_pk` — сверить с актуальной схемой `core/models/catalog/`; если отличаются — поправить INSERT. Структура seed — образец `tests/integration/test_observer_db.py`.)

- [ ] **Step 2: Прогнать — падает**

Run (изолированная БД): `python -m pytest tests/integration/test_alert_retry_sweep.py -q`
Expected: FAIL — `sweep_orphan_alerts` отсутствует.

- [ ] **Step 3: Реализация**

В `core/telegram/alert_dispatcher.py` — `sweep_orphan_alerts`: SELECT `alert_events` за `hours` где НЕ EXISTS соответствующего `telegram_message_refs`:
```sql
SELECT e.id, e.ad_id, e.stage, e.open_state_token, e.scan_id, e.created_at, ...
FROM alert_events e
JOIN fb_ads a ON a.id = e.ad_id
LEFT JOIN fb_adsets ...
WHERE e.created_at >= NOW() - make_interval(hours => :h)
  AND NOT EXISTS (
      SELECT 1 FROM telegram_message_refs r
      WHERE r.ad_id = e.ad_id
        AND r.incident_key = e.open_state_token::text
        AND r.stream_kind = e.stage
  )
ORDER BY e.created_at
```
Для каждого — тот же pre-claim INSERT + send + UPDATE/DELETE, что в `dispatch_pending_alerts` (вынести общий `_deliver_one_alert(...)` если разумно, иначе повторить). Вызывать `sweep_orphan_alerts` в `_run_account_scan` КАЖДЫЙ цикл (не завязано на счётчики). Передать `redis_client` в `dispatch_pending_alerts` (одна строка) — чинит publish `fb_agent:alert:created`.

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/integration/test_alert_retry_sweep.py -q && ruff check core/telegram/alert_dispatcher.py apps/observer_worker/main.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add core/telegram/alert_dispatcher.py apps/observer_worker/main.py tests/integration/test_alert_retry_sweep.py
git commit -m "fix(alert_dispatcher): retry-sweep осиротевших алертов + проброс redis_client"
```

---

### Task 8: TelegramBotClient — retry 5xx

**Files:**
- Modify: `core/telegram/client.py` (`_do_request`: 502/503/504 → backoff retry)
- Test: `tests/unit/test_tg_client_5xx_retry.py`

**Interfaces:** поведение `_do_request` без смены сигнатуры; добавить ретрай при `status in (502,503,504)`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/unit/test_tg_client_5xx_retry.py
# -*- coding: utf-8 -*-
"""TelegramBotClient ретраит 503 (1 повтор), затем возвращает успешный ответ."""
from __future__ import annotations
from unittest.mock import AsyncMock
import httpx
import pytest
from core.telegram.client import TelegramBotClient


# 503 затем 200 → один ретрай, итог 200
@pytest.mark.asyncio
async def test_retries_on_503(monkeypatch):
    client = TelegramBotClient("T")
    calls = []
    async def fake_post(url, json):
        calls.append(1)
        status = 503 if len(calls) == 1 else 200
        return httpx.Response(status, json={"ok": status == 200, "result": {}})
    monkeypatch.setattr(client._http, "post", fake_post)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())  # не ждать реально
    resp = await client._do_request("sendMessage", payload={"chat_id": "1", "text": "x"})
    assert resp.status_code == 200
    assert len(calls) == 2
```

- [ ] **Step 2: Прогнать — падает**

Run: `python -m pytest tests/unit/test_tg_client_5xx_retry.py -q`
Expected: FAIL — текущий `_do_request` не ретраит 503 (возвращает первый ответ 503).

- [ ] **Step 3: Реализация**

В `_do_request` после проверки 429 добавить ретрай для 5xx (один-два повтора с короткой паузой):
```python
        if resp.status_code in (502, 503, 504):
            for delay in (2.0, 5.0):
                await asyncio.sleep(delay)
                resp = await self._http.post(url, json=payload)
                if resp.status_code not in (502, 503, 504):
                    break
        return resp
```
(вставить так, чтобы не конфликтовать с 429-веткой; 429 обрабатывается отдельно выше.)

- [ ] **Step 4: Прогнать — зелёные + ruff**

Run: `python -m pytest tests/unit/test_tg_client_5xx_retry.py tests/unit/test_telegram_renderer.py -q && ruff check core/telegram/client.py tests/unit/test_tg_client_5xx_retry.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add core/telegram/client.py tests/unit/test_tg_client_5xx_retry.py
git commit -m "fix(telegram client): retry 502/503/504 с backoff (раньше только 429)"
```

---

## Финальная верификация (после всех задач)

- [ ] `python -m pytest tests/unit -q` — весь unit-набор зелёный, ruff чисто.
- [ ] Рестарт затронутых воркеров через supervisord: `meta_api_worker`, `observer_worker`, `cabinet_scheduler`, `health_watchdog`, `enable_recommendation_worker` (`.venv/bin/supervisorctl -c supervisord.conf restart ...`).
- [ ] Живая проверка (после онбординга owner-recipient через `/start <invite>`): симулировать провал money-мутации (или дождаться реального) → owner получает DM. До онбординга — нотификации no-op (ожидаемо).

## Замечания по согласованности (для волны 2)

- Волна 1 вводит DM-канал (`notify_owners` → owner-recipients) для НОВЫХ нотификаций; существующие пути (channel-down в meta_api `AutostopAlertContext`, health/enable, alert_dispatcher) пока шлют в `chat_id` + ops-thread. Это временно два канала — **волна 2** унифицирует: переведёт `AutostopAlertContext`, health, enable, alert_dispatcher на тот же DM-адресат, уберёт супергруппу/форум-топики.
- **Промежуточный алерт «авто-стоп ретраит N мин»** частично уже покрыт существующим `maybe_alert_autostop_channel_down` (срабатывает при network-down во время bot_auto_stop). Долгое зависание из-за Meta-side rate-limit (не network-down) промежуточно не алертит — осознанная граница волны 1: финальный провал (~1ч, Task 2) всё равно доедет до owner'а. Отдельный rate-limit-промежуточный алерт — кандидат на волну 4, не money-критичен.
- `notify_owners` — точка абстракции: при переходе на иную модель адресата (волна 2) меняется только её внутренность, вызовы воркеров стабильны.
