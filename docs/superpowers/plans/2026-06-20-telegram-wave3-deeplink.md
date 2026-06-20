# Telegram Волна 3 — web_app deep-link кнопки под алертами Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Под алертами warning/stop и enable_reco в личке добавить web_app-кнопку «🔎 Открыть в Mini App», тап которой открывает Mini App на `{base}/ads/{fb_ad_id}`; провести live-аудит туннеля и зафиксировать runbook.

**Architecture:** Чистый прокид `web_app_base: str | None` через рендереры алертов. Единая pure-функция нормализации base в `core/telegram/web_app_url.py`. Dispatcher грузит `web_app_url` из `system_config` один раз на батч и прокидывает в `AlertRenderInput`. enable_reco worker делает то же для `EnableRecoRenderInput`. Кнопка добавляется только при наличии https-base (graceful при отсутствии туннеля). Touch-points минимальны, существующие callback-кнопки (`dis:`, `ereco:`) не трогаются.

**Tech Stack:** Python 3.12, dataclasses (frozen), pytest/pytest-asyncio, SQLAlchemy async, Telegram Bot API (inline `web_app` кнопки), cloudflared (live-аудит).

## Global Constraints

- web_app-кнопка добавляется **только** при `web_app_base`, начинающемся с `https://`; иначе кнопка опущена (graceful — туннеля может не быть).
- URL deep-link строго `f"{web_app_base}/ads/{fb_ad_id}"` (base уже включает префикс `/tma`, без хвостового слэша).
- web_app-кнопка — **отдельной строкой НАД** существующей callback-кнопкой (`🛑 Отключить` / `▶️ Включить`); существующие callback-кнопки и их `callback_data` не меняются.
- Текст web_app-кнопки строго: `🔎 Открыть в Mini App`.
- Нормализация base — единый источник `normalize_web_app_base` в `core/telegram/web_app_url.py`; не дублировать https-guard/strip в dispatcher и worker.
- web_app-кнопка под алертами добавляется при `stage in ('warning', 'stop')` (там же, где сейчас «Отключить»).
- Все комментарии/сообщения/тесты — по-русски; короткий русский комментарий над каждым тестом с описанием сценария.
- Ruff: line-length=100, target py312. Никаких файлов >500 строк в новом коде.

---

### Task 1: `normalize_web_app_base` — единый нормализатор base

**Files:**
- Modify: `core/telegram/web_app_url.py` (добавить функцию + в `__all__`)
- Test: `tests/unit/test_web_app_url.py` (создать)

**Interfaces:**
- Produces: `normalize_web_app_base(raw: str | None) -> str | None` — возвращает https-base без хвостового `/`, либо `None` если пусто/не-https.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/unit/test_web_app_url.py`:

```python
# -*- coding: utf-8 -*-
"""Юнит-тесты normalize_web_app_base — нормализация base для deep-link кнопок."""

from __future__ import annotations

import pytest

from core.telegram.web_app_url import normalize_web_app_base


# https-base: обрезаются пробелы и хвостовой слэш
def test_https_strips_whitespace_and_trailing_slash():
    assert normalize_web_app_base("  https://h.ts.net/tma/  ") == "https://h.ts.net/tma"


# https без хвостового слэша возвращается как есть
def test_https_passthrough():
    assert normalize_web_app_base("https://h.ts.net/tma") == "https://h.ts.net/tma"


# http (не https) отвергается → None (Telegram требует https)
def test_http_rejected():
    assert normalize_web_app_base("http://h.ts.net/tma") is None


# пусто / None / только пробелы → None
@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_returns_none(raw):
    assert normalize_web_app_base(raw) is None
```

- [ ] **Step 2: Прогнать тест — убедиться, что падает**

Run: `pytest tests/unit/test_web_app_url.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_web_app_base'`

- [ ] **Step 3: Реализовать функцию**

В `core/telegram/web_app_url.py` добавить перед `__all__`:

```python
def normalize_web_app_base(raw: str | None) -> str | None:
    """Нормализует web_app base для deep-link кнопок под алертами.

    Возвращает https-base без хвостового слэша, либо None если raw пуст
    или не https (Telegram inline web_app кнопки требуют https-URL).
    """
    if not raw:
        return None
    cleaned = raw.strip().rstrip("/")
    if not cleaned.startswith("https://"):
        return None
    return cleaned
```

И обновить `__all__`:

```python
__all__ = ["load_web_app_url", "save_web_app_url", "normalize_web_app_base"]
```

- [ ] **Step 4: Прогнать тест — убедиться, что проходит**

Run: `pytest tests/unit/test_web_app_url.py -v`
Expected: PASS (4 passed — параметризация даёт 6 кейсов)

- [ ] **Step 5: Commit**

```bash
git add core/telegram/web_app_url.py tests/unit/test_web_app_url.py
git commit -m "feat(telegram): normalize_web_app_base — единый нормализатор base для deep-link"
```

---

### Task 2: web_app-кнопка под warning/stop алертами

**Files:**
- Modify: `core/telegram/renderer.py` (поле `web_app_base` в `AlertRenderInput`; `render_inline_keyboard`)
- Test: `tests/unit/test_telegram_renderer.py` (добавить тест-функции)

**Interfaces:**
- Consumes: ничего из Task 1 напрямую (рендерер получает уже нормализованный base, но защищается своим https-guard).
- Produces: `AlertRenderInput.web_app_base: str | None = None`; `render_inline_keyboard` при https-base возвращает клавиатуру с web_app-строкой первой.

- [ ] **Step 1: Написать падающий тест**

В конец `tests/unit/test_telegram_renderer.py` добавить:

```python
from core.telegram.renderer import AlertRenderInput, render_inline_keyboard


def _stop_input(**over):
    """Базовый STOP-инпут для тестов клавиатуры (поля переопределяются через over)."""
    base = dict(
        fb_ad_id="900",
        ad_name="Ad",
        campaign_name="CR2|KE",
        adset_name="EQ",
        offer_code="KE",
        stage="stop",
        matched_rule_codes=[],
        metrics={},
        open_state_token="tok12345abc",
    )
    base.update(over)
    return AlertRenderInput(**base)


# web_app-кнопка присутствует первой строкой и ведёт на /ads/{fb_ad_id}
def test_keyboard_has_web_app_button_when_base_set():
    kb = render_inline_keyboard(_stop_input(web_app_base="https://h.ts.net/tma"))
    rows = kb["inline_keyboard"]
    assert rows[0][0]["text"] == "🔎 Открыть в Mini App"
    assert rows[0][0]["web_app"]["url"] == "https://h.ts.net/tma/ads/900"
    # «Отключить» — отдельной строкой ниже, callback не изменён
    assert rows[1][0]["text"] == "🛑 Отключить"
    assert rows[1][0]["callback_data"] == "dis:900:tok12345"


# без base — web_app-кнопки нет, только «Отключить» (текущее поведение)
def test_keyboard_no_web_app_button_when_base_none():
    kb = render_inline_keyboard(_stop_input(web_app_base=None))
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "🛑 Отключить"


# не-https base игнорируется (Telegram требует https) → web_app-кнопки нет
def test_keyboard_no_web_app_button_when_base_not_https():
    kb = render_inline_keyboard(_stop_input(web_app_base="http://h.ts.net/tma"))
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "🛑 Отключить"
```

- [ ] **Step 2: Прогнать тест — убедиться, что падает**

Run: `pytest tests/unit/test_telegram_renderer.py -k web_app -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'web_app_base'`

- [ ] **Step 3: Реализовать**

В `core/telegram/renderer.py` в `AlertRenderInput` добавить поле последним:

```python
@dataclass(frozen=True)
class AlertRenderInput:
    """Минимум данных нужный чтобы отрендерить алерт."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    stage: str  # 'warning' | 'stop'
    matched_rule_codes: list[str]
    metrics: dict[str, Any]
    open_state_token: str | None  # для callback кнопок
    web_app_base: str | None = None  # https-base Mini App для deep-link кнопки
```

Переписать `render_inline_keyboard`:

```python
def render_inline_keyboard(inp: AlertRenderInput) -> dict | None:
    """Inline-клавиатура с кнопками действий.

    При заданном https web_app_base первой строкой добавляется web_app-кнопка
    «Открыть в Mini App» (deep-link на /ads/{fb_ad_id}); ниже — callback 'dis'.

    Callback data format: `<action>:<fb_ad_id>:<token>` где action:
    - 'dis'   — отключить

    Snooze убран (решение владельца). Telegram limit на callback_data = 64 bytes.
    """
    token_short = (inp.open_state_token or "")[:8]
    buttons: list[list[dict]] = []

    if inp.stage in ("warning", "stop"):
        if inp.web_app_base and inp.web_app_base.startswith("https://"):
            buttons.append(
                [
                    {
                        "text": "🔎 Открыть в Mini App",
                        "web_app": {"url": f"{inp.web_app_base}/ads/{inp.fb_ad_id}"},
                    },
                ]
            )
        buttons.append(
            [
                {
                    "text": "🛑 Отключить",
                    "callback_data": f"dis:{inp.fb_ad_id}:{token_short}",
                },
            ]
        )
    if not buttons:
        return None
    return {"inline_keyboard": buttons}
```

- [ ] **Step 4: Прогнать тесты — убедиться, что проходят**

Run: `pytest tests/unit/test_telegram_renderer.py -v`
Expected: PASS (включая существующие тесты renderer — регресс зелёный)

- [ ] **Step 5: Commit**

```bash
git add core/telegram/renderer.py tests/unit/test_telegram_renderer.py
git commit -m "feat(telegram): web_app deep-link кнопка под warning/stop алертами"
```

---

### Task 3: Прокид web_app_base через alert_dispatcher

**Files:**
- Modify: `core/telegram/alert_dispatcher.py` (импорт; helper `_resolve_web_app_base`; kwarg в `_deliver_one_alert`; загрузка в `dispatch_pending_alerts` и `sweep_orphan_alerts`)
- Test: `tests/integration/test_dispatch_web_app_button.py` (создать)

**Interfaces:**
- Consumes: `normalize_web_app_base` (Task 1), `load_web_app_url` (существует), `AlertRenderInput.web_app_base` (Task 2).
- Produces: `_resolve_web_app_base(engine) -> str | None`; `_deliver_one_alert(..., web_app_base: str | None = None)`.

- [ ] **Step 1: Написать падающий integration-тест**

Создать `tests/integration/test_dispatch_web_app_button.py`:

```python
# -*- coding: utf-8 -*-
"""dispatch добавляет web_app deep-link кнопку под алертом при заданном web_app_url.

Волна 3: при наличии https web_app_url в system_config клавиатура алерта содержит
кнопку «🔎 Открыть в Mini App» с URL {base}/ads/{fb_ad_id}; при отсутствии — нет.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.alert_dispatcher import dispatch_pending_alerts
from core.telegram.web_app_url import save_web_app_url


async def _seed_tg_config_no_chat(conn) -> None:
    from core.crypto import encrypt

    enc = encrypt("TEST_BOT_TOKEN_FAKE")
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, chat_id, poller_offset)
            VALUES ('default', :tok, NULL, 0)
            ON CONFLICT (singleton_key) DO UPDATE
            SET bot_token_encrypted = EXCLUDED.bot_token_encrypted, chat_id = NULL
            """
        ),
        {"tok": enc},
    )


@pytest_asyncio.fixture
async def _seed(pg_engine):
    """1 recipient + fb_ad '900' + STOP alert_event (scan_id=31)."""
    ad_id = uuid.uuid4()
    tok = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for t in (
            "telegram_message_refs",
            "telegram_recipients",
            "alert_events",
            "fb_ads",
            "fb_adsets",
            "fb_campaigns",
            "telegram_config",
        ):
            await conn.execute(text(f"DELETE FROM {t}"))
        await _seed_tg_config_no_chat(conn)
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients (id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), 111, 111, 'recipient')"
            )
        )
        cid_c = uuid.uuid4()
        sid = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, last_seen_at) "
                "VALUES (:i, 'c', 'CR2|KE', NOW())"
            ),
            {"i": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, fb_adset_id, adset_name, campaign_id, last_seen_at) "
                "VALUES (:i, 's', 'EQ', :c, NOW())"
            ),
            {"i": sid, "c": cid_c},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, fb_ad_id, ad_name, adset_id, last_seen_at) "
                "VALUES (:i, '900', 'Ad', :s, NOW())"
            ),
            {"i": ad_id, "s": sid},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, "
                "open_state_token, scan_id, created_at) "
                "VALUES (gen_random_uuid(), :ad, 'stop', 'stop_sent', '[]'::jsonb, "
                "'{}'::jsonb, :tok, 31, NOW())"
            ),
            {"ad": ad_id, "tok": tok},
        )
    return {"ad_id": ad_id}


def _reply_markup(client) -> dict | None:
    """reply_markup из последнего вызова send_message."""
    return client.send_message.await_args.kwargs["reply_markup"]


# web_app_url задан → клавиатура содержит web_app deep-link кнопку на /ads/900
@pytest.mark.asyncio
async def test_web_app_button_present_when_url_set(pg_engine, _seed):
    await save_web_app_url(pg_engine, "https://h.ts.net/tma")
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})

    await dispatch_pending_alerts(pg_engine, client=client, scan_id=31, redis_client=None)

    rows = _reply_markup(client)["inline_keyboard"]
    assert rows[0][0]["text"] == "🔎 Открыть в Mini App"
    assert rows[0][0]["web_app"]["url"] == "https://h.ts.net/tma/ads/900"
    assert rows[1][0]["text"] == "🛑 Отключить"


# web_app_url пуст → web_app-кнопки нет, только «Отключить» (graceful)
@pytest.mark.asyncio
async def test_web_app_button_absent_when_url_unset(pg_engine, _seed):
    await save_web_app_url(pg_engine, None)
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"message_id": 5})

    await dispatch_pending_alerts(pg_engine, client=client, scan_id=31, redis_client=None)

    rows = _reply_markup(client)["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "🛑 Отключить"
```

- [ ] **Step 2: Прогнать тест — убедиться, что падает**

Run: `pytest tests/integration/test_dispatch_web_app_button.py -v`
Expected: FAIL (`test_web_app_button_present_when_url_set` падает — web_app-кнопки нет, т.к. base не прокинут)

- [ ] **Step 3: Реализовать прокид**

В `core/telegram/alert_dispatcher.py`:

(a) Добавить импорт после строки 29 (`from core.telegram.service import ...`):

```python
from core.telegram.web_app_url import load_web_app_url, normalize_web_app_base
```

(b) Добавить helper перед `async def _deliver_one_alert` (около строки 116):

```python
async def _resolve_web_app_base(engine: AsyncEngine) -> str | None:
    """web_app base для deep-link кнопок: system_config.web_app_url → нормализация."""
    return normalize_web_app_base(await load_web_app_url(engine))
```

(c) В сигнатуре `_deliver_one_alert` добавить kwarg (после `counters: dict[str, int],`):

```python
    counters: dict[str, int],
    web_app_base: str | None = None,
) -> None:
```

(d) В `_deliver_one_alert` в конструктор `AlertRenderInput` добавить поле (после `open_state_token=...`):

```python
    render_input = AlertRenderInput(
        fb_ad_id=str(fb_ad_id),
        ad_name=str(ad_name or ""),
        campaign_name=str(campaign_name or ""),
        adset_name=str(adset_name or ""),
        offer_code=str(offer_code) if offer_code else None,
        stage=str(stage),
        matched_rule_codes=list(matched_codes or []),
        metrics=dict(metrics_json or {}),
        open_state_token=str(open_token) if open_token else None,
        web_app_base=web_app_base,
    )
```

(e) В `dispatch_pending_alerts`: после блока с `recipients`/guard (после строки 284, перед `thread_id_by_stage`) добавить:

```python
    # Волна 3: web_app deep-link base — грузим один раз на батч (не per-alert).
    web_app_base = await _resolve_web_app_base(engine)
```

И в вызов `_deliver_one_alert` (около строки 350) добавить последним kwarg:

```python
                incident_key=incident_key,
                counters=counters,
                web_app_base=web_app_base,
            )
```

(f) В `sweep_orphan_alerts`: симметрично — после загрузки `recipients` (строка 398) добавить ту же строку `web_app_base = await _resolve_web_app_base(engine)`, и в вызов `_deliver_one_alert` (около строки 479) добавить `web_app_base=web_app_base,` последним kwarg.

- [ ] **Step 4: Прогнать тесты — убедиться, что проходят**

Run: `pytest tests/integration/test_dispatch_web_app_button.py tests/integration/test_dispatch_broadcast.py -v`
Expected: PASS (новые + регресс broadcast зелёные)

- [ ] **Step 5: Commit**

```bash
git add core/telegram/alert_dispatcher.py tests/integration/test_dispatch_web_app_button.py
git commit -m "feat(telegram): прокид web_app_base в dispatch/sweep → deep-link кнопка под алертами"
```

---

### Task 4: web_app-кнопка под enable_reco алертами

**Files:**
- Modify: `core/enable_reco/alert.py` (поле `web_app_base` в `EnableRecoRenderInput`; web_app-строка в `render_enable_reco_alert`)
- Modify: `apps/enable_recommendation_worker/main.py` (загрузка `web_app_url` + передача base в `EnableRecoRenderInput`)
- Test: `tests/unit/test_enable_reco_alert.py` (создать)

**Interfaces:**
- Consumes: `normalize_web_app_base` + `load_web_app_url` (Task 1 / существует).
- Produces: `EnableRecoRenderInput.web_app_base: str | None = None`; `render_enable_reco_alert` при https-base добавляет web_app-строку над «Включить».

- [ ] **Step 1: Написать падающий тест**

Создать `tests/unit/test_enable_reco_alert.py`:

```python
# -*- coding: utf-8 -*-
"""Юнит-тесты web_app deep-link кнопки под enable_reco алертом."""

from __future__ import annotations

from core.enable_reco.alert import EnableRecoRenderInput, render_enable_reco_alert
from core.enable_reco.analyzer import RecommendationDecision


def _decision() -> RecommendationDecision:
    """Минимальное решение-рекомендация для рендера (level=warning).

    Поля сверены с core/enable_reco/analyzer.py::RecommendationDecision:
    recommend/level/reasons(tuple)/skip_reason/snapshot. reasons — tuple.
    """
    return RecommendationDecision(
        recommend=True, level="warning", reasons=("CPL выправился",), snapshot={}
    )


def _inp(**over) -> EnableRecoRenderInput:
    base = dict(
        fb_ad_id="900",
        ad_name="Ad",
        campaign_name="CR2|KE",
        adset_name="EQ",
        offer_code="KE",
        decision=_decision(),
    )
    base.update(over)
    return EnableRecoRenderInput(**base)


# при https-base web_app-кнопка идёт первой строкой и ведёт на /ads/900
def test_web_app_button_present_when_base_set():
    _text, markup = render_enable_reco_alert(_inp(web_app_base="https://h.ts.net/tma"))
    rows = markup["inline_keyboard"]
    assert rows[0][0]["text"] == "🔎 Открыть в Mini App"
    assert rows[0][0]["web_app"]["url"] == "https://h.ts.net/tma/ads/900"
    assert rows[1][0]["text"] == "▶️ Включить"


# без base — только «Включить» (текущее поведение)
def test_web_app_button_absent_when_base_none():
    _text, markup = render_enable_reco_alert(_inp(web_app_base=None))
    rows = markup["inline_keyboard"]
    assert len(rows) == 1
    assert rows[0][0]["text"] == "▶️ Включить"
```

- [ ] **Step 2: Прогнать тест — убедиться, что падает**

Run: `pytest tests/unit/test_enable_reco_alert.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'web_app_base'`

- [ ] **Step 3: Реализовать**

В `core/enable_reco/alert.py` в `EnableRecoRenderInput` добавить поле последним:

```python
@dataclass(frozen=True)
class EnableRecoRenderInput:
    """Данные для рендеринга алерта."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    decision: RecommendationDecision
    web_app_base: str | None = None  # https-base Mini App для deep-link кнопки
```

В `render_enable_reco_alert` заменить блок построения `reply_markup` на:

```python
    rows: list[list[dict]] = []
    if inp.web_app_base and inp.web_app_base.startswith("https://"):
        rows.append(
            [
                {
                    "text": "🔎 Открыть в Mini App",
                    "web_app": {"url": f"{inp.web_app_base}/ads/{inp.fb_ad_id}"},
                }
            ]
        )
    rows.append(
        [
            {
                "text": "▶️ Включить",
                "callback_data": build_enable_reco_callback(inp.fb_ad_id),
            }
        ]
    )
    reply_markup = {"inline_keyboard": rows}
    return text, reply_markup
```

- [ ] **Step 4: Прогнать тест — убедиться, что проходит**

Run: `pytest tests/unit/test_enable_reco_alert.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Прокинуть base в worker**

В `apps/enable_recommendation_worker/main.py`:

(a) Добавить импорт (рядом с другими `from core.telegram...`):

```python
from core.telegram.web_app_url import load_web_app_url, normalize_web_app_base
```

(b) В `send_alert` (около строки 278) перед построением `EnableRecoRenderInput` загрузить base и передать его:

```python
    web_app_base = normalize_web_app_base(await load_web_app_url(engine))
    text_body, reply_markup = render_enable_reco_alert(
        EnableRecoRenderInput(
            fb_ad_id=candidate.fb_ad_id,
            ad_name=candidate.ad_name,
            campaign_name=candidate.campaign_name,
            adset_name=candidate.adset_name,
            offer_code=candidate.offer_code,
            decision=decision,
            web_app_base=web_app_base,
        )
    )
```

- [ ] **Step 6: Прогнать регресс enable_reco worker**

Run: `pytest tests/unit/test_enable_reco_alert.py tests/integration/test_enable_reco_worker.py -v`
Expected: PASS (новый unit + регресс воркера зелёные)

- [ ] **Step 7: Commit**

```bash
git add core/enable_reco/alert.py apps/enable_recommendation_worker/main.py tests/unit/test_enable_reco_alert.py
git commit -m "feat(enable_reco): web_app deep-link кнопка под рекомендацией включения"
```

---

### Task 5: Live-аудит туннеля + runbook web_app-кнопки

**Files:**
- Modify: `docs/mini_app_tunnel.md` (секция «Волна 3 — чеклист web_app-кнопки»)
- (Без кода — проверочная задача + документация)

**Interfaces:**
- Consumes: рабочий mini-сервер на `MINI_PORT=5175` (из `run.sh`), `cloudflared` (установлен), реализованные web_app-кнопки (Task 2-4).

- [ ] **Step 1: Поднять mini-сервер для проверки (если ещё не запущен)**

Проверить, что mini отвечает локально (его запускает `run.sh` из терминала пользователя):

Run: `curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:5175/tma/`
Expected: `200` (если не 200 — попросить пользователя поднять mini через `run.sh`; standalone vite поднимать из агента не нужно).

- [ ] **Step 2: Поднять cloudflared quick-tunnel на mini-порт**

Run:
```bash
cloudflared tunnel --url http://localhost:5175 --no-autoupdate > /tmp/cf_wave3.log 2>&1 &
sleep 12
grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf_wave3.log | head -1
```
Expected: печатает URL вида `https://<random>.trycloudflare.com`.

- [ ] **Step 3: curl-проверка deep-link через туннель**

Подставить URL из Step 2 в `TUN`:
```bash
TUN="https://<random>.trycloudflare.com"
curl -sS -o /dev/null -w "%{http_code}\n" "$TUN/tma/ads/900"
```
Expected: `200` (SPA index отдаётся для произвольного пути → deep-link доедет до роутера Mini App).

- [ ] **Step 4: Погасить тестовый туннель**

Run: `pkill -f "cloudflared tunnel --url http://localhost:5175" || true`
Expected: процесс завершён (эфемерный туннель не нужен в проде — стабильный путь через Tailscale Funnel).

- [ ] **Step 5: Дополнить runbook**

В `docs/mini_app_tunnel.md` в конец добавить секцию:

```markdown
## Волна 3 — чеклист web_app-кнопки под алертами

Под warning/stop и enable_reco алертами в личке есть кнопка «🔎 Открыть в Mini App»
(deep-link на `{base}/ads/{fb_ad_id}`). Появляется только при заданном https
`web_app_url` (через Tailscale Funnel или cloudflared); без туннеля — graceful опущена.

Проверка стабильного пути (разово, на телефоне):
1. `tailscale up` — браузер-логин в свой аккаунт.
2. `./scripts/setup_tailscale_funnel.sh` — стабильный URL + авто `web_app_url` в БД
   + авто Menu Button.
3. BotFather → Bot Settings → Menu Button → вставить `https://<host>.<tailnet>.ts.net/tma/`.
4. Дождаться реального warning/stop алерта в личке → тапнуть «🔎 Открыть в Mini App»
   → Mini App открывается на нужном объявлении (`/tma/ads/<id>`).

Быстрая проверка deep-link без телефона (эфемерный туннель):
```bash
cloudflared tunnel --url http://localhost:5175 --no-autoupdate &
# взять напечатанный https://<...>.trycloudflare.com
curl -sS -o /dev/null -w "%{http_code}\n" "https://<...>.trycloudflare.com/tma/ads/<any_id>"  # → 200
```
```

- [ ] **Step 6: Commit**

```bash
git add docs/mini_app_tunnel.md
git commit -m "docs(wave3): runbook-чеклист web_app deep-link кнопки + live-аудит туннеля"
```

---

## Финальная проверка (после всех задач)

- [ ] Полный unit + относящийся integration: `pytest tests/unit/test_web_app_url.py tests/unit/test_telegram_renderer.py tests/unit/test_enable_reco_alert.py tests/integration/test_dispatch_web_app_button.py tests/integration/test_dispatch_broadcast.py tests/integration/test_enable_reco_worker.py -v` — всё зелёное.
- [ ] `ruff check core/telegram/ core/enable_reco/ apps/enable_recommendation_worker/ tests/unit/test_web_app_url.py tests/unit/test_enable_reco_alert.py tests/integration/test_dispatch_web_app_button.py` — чисто.
- [ ] Финальный opus broad-review (money-нейтрально, но money-смежно: убедиться, что web_app-кнопка не ломает доставку алертов при отсутствии туннеля; callback `dis:`/`ereco:` не задеты; graceful-путь сохранён).

## Деплой (после merge)

- Рестарт затронутых воркеров: observer (через alert_dispatcher), enable_recommendation_worker. Миграций нет (поле `web_app_url` уже в `system_config`). web_app-кнопки появятся, как только `web_app_url` будет задан (Tailscale Funnel / cloudflared в `run.sh`).
