# Recorder Fix + Analyzer Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Починить запись событий в Ads Manager (сейчас все JSON пустые) и переписать analyzer так, чтобы по записи получать markdown-отчёт с ранжированными селекторами для ручного написания шагов `core/campaign_creator/`.

**Architecture:** Три изолированных блока: (1) диагностика инжекта + session_id + sanity-check, (2) расширение JS-сборщика (label, placeholder, heading, selector_candidates), (3) переписанный analyzer с денойзом → `UserAction` → markdown-отчёт + новый `/analyze` endpoint + UI-индикатор.

**Tech Stack:** Python 3.12 (async), Playwright CDP, FastAPI, Pydantic v2, pytest+pytest-asyncio, React 19 + Vite.

**Spec:** `docs/superpowers/specs/2026-05-13-recorder-fix-and-analyzer-design.md`

---

## File Structure

```
core/campaign_recorder/
  cdp_session.py        [edit] логирование всех контекстов и URL
  event_injector.py     [edit] session_id, InjectionReport, расширенные поля
  analyzer.py           [rewrite] денойз → list[UserAction]
  markdown_report.py    [new]    build_markdown(session, actions) -> str
apps/api/
  routers/campaign_recorder.py  [edit] injection_report в /status, новый /analyze
  schemas.py                    [edit] injection_ok, target_url, новый Analyze response
frontend/src/
  api.js                         [edit] обновить парс /analyze
  pages/ScriptsPage.jsx          [edit] indicator + md viewer + download
tests/unit/
  test_campaign_recorder.py      [edit] адаптировать под новый формат
  test_analyzer_denoise.py       [new]
  test_markdown_report.py        [new]
  test_injection_report.py       [new]
```

---

## Block 1 — Diagnostics & Recording Fix

### Task 1: Логирование контекстов в CdpSession

**Files:**
- Modify: `core/campaign_recorder/cdp_session.py`

- [ ] **Step 1: Добавить логирование всех контекстов и страниц после connect_over_cdp**

В `cdp_session.py` после строки `browser = await pw.chromium.connect_over_cdp(cdp_url)` и до `page = _pick_target_page(browser)` добавить:

```python
            logger.info(
                "CDP контекстов в браузере: %d", len(browser.contexts)
            )
            for ci, ctx in enumerate(browser.contexts):
                for pi, p in enumerate(ctx.pages):
                    logger.info(
                        "  ctx[%d] page[%d] url=%s frames=%d",
                        ci, pi, p.url, len(p.frames),
                    )
```

- [ ] **Step 2: Зафиксировать коммитом**

```bash
git add core/campaign_recorder/cdp_session.py
git commit -m "feat(recorder): log contexts and pages on CDP connect"
```

---

### Task 2: session_id в JS-инжекторе

**Files:**
- Modify: `core/campaign_recorder/event_injector.py`
- Test: `tests/unit/test_campaign_recorder.py`

- [ ] **Step 1: Обновить тест JS-инжектора под параметр session_id**

В `tests/unit/test_campaign_recorder.py` заменить `test_injector_js_contains_event_listeners`:

```python
def test_injector_js_contains_event_listeners():
    """JS-сниппет должен слушать click, input, change, keydown, submit и проставлять session_id."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR

    js = BUILD_JS_INJECTOR("test-session-id")
    for event in ["click", "input", "change", "keydown", "submit"]:
        assert event in js, f"JS не содержит обработчик события {event}"
    assert "test-session-id" in js
    assert "session_id" in js
```

- [ ] **Step 2: Запустить тест — должен упасть**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_contains_event_listeners -v
```

Expected: FAIL — `BUILD_JS_INJECTOR()` пока не принимает аргумент.

- [ ] **Step 3: Сделать BUILD_JS_INJECTOR(session_id) и проставлять session_id на window**

В `core/campaign_recorder/event_injector.py` изменить сигнатуру:

```python
def BUILD_JS_INJECTOR(session_id: str = "") -> str:
    """Возвращает JS-сниппет, который собирает события DOM."""
    return f"""
(function() {{
  try {{
    var SESSION_ID = {repr(session_id)};
    if (window.__fbRecorder && window.__fbRecorder.installed && window.__fbRecorder.session_id === SESSION_ID) return;
    window.__fbRecorder = window.__fbRecorder || {{ events: [] }};
    window.__fbRecorder.installed = true;
    window.__fbRecorder.session_id = SESSION_ID;
""" + _JS_BODY + """
  }} catch (e) {{
    /* swallow */
  }}
}})();
"""
```

Где `_JS_BODY` — старое тело начиная с `function safe(...)` до закрывающей `}})();` (без внешней обёртки). Текущее тело перенести в строковую константу `_JS_BODY` в модуле. Для минимального дифа можно оставить одну строку — добавить присваивание `window.__fbRecorder.session_id = SESSION_ID;` сразу после `window.__fbRecorder.installed = true;` и принять параметр.

Минимальный вариант — patch строки:

```python
def BUILD_JS_INJECTOR(session_id: str = "") -> str:
    js_sid = repr(session_id)
    return f"""
(function() {{
  try {{
    var SESSION_ID = {js_sid};
    if (window.__fbRecorder && window.__fbRecorder.installed && window.__fbRecorder.session_id === SESSION_ID) return;
    window.__fbRecorder = window.__fbRecorder || {{ events: [] }};
    window.__fbRecorder.installed = true;
    window.__fbRecorder.session_id = SESSION_ID;
    // ... остальное тело без изменений ...
```

Адаптировать существующий return (заменить начало, оставить тело).

- [ ] **Step 4: Обновить attach_recorder и inject_event_listener чтобы принимать session_id**

В том же файле:

```python
async def _inject_into_frame(frame: Frame, session_id: str = "") -> None:
    try:
        await frame.evaluate(BUILD_JS_INJECTOR(session_id))
    except Exception as exc:
        logger.debug("Не удалось инжектить в фрейм %s: %s", getattr(frame, "url", "?"), exc)


async def inject_into_page(page: Page, session_id: str = "") -> None:
    for frame in page.frames:
        await _inject_into_frame(frame, session_id)


async def attach_recorder(context: BrowserContext, session_id: str = "") -> "InjectionReport":
    js = BUILD_JS_INJECTOR(session_id)
    try:
        await context.add_init_script(js)
    except Exception as exc:
        logger.warning("Не удалось зарегистрировать init_script: %s", exc)

    for page in context.pages:
        await inject_into_page(page, session_id)

    def _on_new_page(page: Page) -> None:
        page.on(
            "framenavigated",
            lambda fr: _safe_create_task(_inject_into_frame(fr, session_id)),
        )
        _safe_create_task(inject_into_page(page, session_id))

    context.on("page", _on_new_page)

    for page in context.pages:
        page.on(
            "framenavigated",
            lambda fr: _safe_create_task(_inject_into_frame(fr, session_id)),
        )

    return await _build_injection_report(context, session_id)
```

- [ ] **Step 5: Запустить тест — должен пройти**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_contains_event_listeners -v
```

Expected: PASS.

- [ ] **Step 6: Зафиксировать**

```bash
git add core/campaign_recorder/event_injector.py tests/unit/test_campaign_recorder.py
git commit -m "feat(recorder): pass session_id into JS injector"
```

---

### Task 3: InjectionReport со sanity-check

**Files:**
- Modify: `core/campaign_recorder/event_injector.py`
- Test: `tests/unit/test_injection_report.py` (new)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/unit/test_injection_report.py`:

```python
"""Тест: attach_recorder возвращает InjectionReport с проверкой sanity-check."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_attach_recorder_returns_injection_report():
    from core.campaign_recorder.event_injector import attach_recorder

    frame_ok = MagicMock(url="https://adsmanager.facebook.com/x")
    frame_ok.evaluate = AsyncMock(return_value=True)
    frame_bad = MagicMock(url="https://adsmanager.facebook.com/y")
    # первое evaluate — установка инжектора, второе — sanity check
    frame_bad.evaluate = AsyncMock(side_effect=[None, False])

    page = MagicMock(
        url="https://adsmanager.facebook.com",
        frames=[frame_ok, frame_bad],
        on=MagicMock(),
    )
    page.on = MagicMock()
    context = MagicMock(pages=[page])
    context.add_init_script = AsyncMock()
    context.on = MagicMock()

    report = await attach_recorder(context, session_id="sid-1")
    assert report.pages, "InjectionReport должен содержать страницы"
    p = report.pages[0]
    assert p.url == "https://adsmanager.facebook.com"
    assert p.frames_total == 2
    assert p.frames_injected == 1
```

- [ ] **Step 2: Запустить — должен упасть**

```bash
pytest tests/unit/test_injection_report.py -v
```

Expected: FAIL — нет `InjectionReport` или поля.

- [ ] **Step 3: Реализовать InjectionReport и sanity-check**

В `core/campaign_recorder/event_injector.py` добавить:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PageInjection:
    url: str
    frames_total: int
    frames_injected: int


@dataclass(frozen=True)
class InjectionReport:
    pages: tuple[PageInjection, ...] = field(default_factory=tuple)

    @property
    def pages_injected(self) -> int:
        return sum(1 for p in self.pages if p.frames_injected > 0)

    @property
    def ok(self) -> bool:
        return self.pages_injected > 0


async def _check_frame_installed(frame: Frame, session_id: str) -> bool:
    try:
        return bool(
            await frame.evaluate(
                "(sid) => !!(window.__fbRecorder && window.__fbRecorder.installed"
                " && window.__fbRecorder.session_id === sid)",
                session_id,
            )
        )
    except Exception:
        return False


async def _build_injection_report(
    context: BrowserContext, session_id: str
) -> InjectionReport:
    pages = []
    for page in context.pages:
        injected = 0
        for frame in page.frames:
            if await _check_frame_installed(frame, session_id):
                injected += 1
            else:
                await _inject_into_frame(frame, session_id)
                if await _check_frame_installed(frame, session_id):
                    injected += 1
        pages.append(
            PageInjection(
                url=page.url or "",
                frames_total=len(page.frames),
                frames_injected=injected,
            )
        )
    return InjectionReport(pages=tuple(pages))
```

Тест ожидает что первый frame_ok вернёт True на sanity → injected=1, frame_bad вернёт None на установку и False на sanity → injected=0. Итого frames_injected=1. Подкорректировать тест если нужен переинжект — мок side_effect должен поддерживать оба вызова.

Уточнить тест под фактическую логику: `_check_frame_installed` сначала вызывается, потом при False делается inject + повторный check. Для frame_ok: evaluate возвращает True. Для frame_bad: первый evaluate (check) → False, второй evaluate (inject) → None, третий evaluate (check) → False. Обновить side_effect.

```python
    frame_ok.evaluate = AsyncMock(return_value=True)
    frame_bad.evaluate = AsyncMock(side_effect=[False, None, False])
```

- [ ] **Step 4: Тест зелёный**

```bash
pytest tests/unit/test_injection_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_recorder/event_injector.py tests/unit/test_injection_report.py
git commit -m "feat(recorder): InjectionReport with per-frame sanity check"
```

---

### Task 4: Использовать InjectionReport в раннере + лог-цикл

**Files:**
- Modify: `apps/api/routers/campaign_recorder.py`

- [ ] **Step 1: Передавать session_id и сохранять отчёт**

В `apps/api/routers/campaign_recorder.py` внутри `_run_session`:

```python
        async with session.connect() as page:
            context = page.context
            report = await attach_recorder(context, session_id=session_id)
            _active_sessions[session_id]["injection_report"] = report
            _active_sessions[session_id]["target_url"] = page.url
            _active_sessions[session_id]["page"] = page
            if not report.ok:
                _active_sessions[session_id]["status"] = "error"
                _active_sessions[session_id]["error"] = (
                    "Ни в один фрейм не удалось инжектить recorder"
                )
                return
            _active_sessions[session_id]["status"] = "recording"
            logger.info(
                "Запись стартовала. session=%s, url=%s, pages_injected=%d",
                session_id, page.url, report.pages_injected,
            )
            stop_event: asyncio.Event = _active_sessions[session_id]["stop_event"]
            tick = 0
            while not stop_event.is_set():
                await asyncio.sleep(1)
                tick += 1
                try:
                    events = await collect_events(context)
                except Exception as poll_exc:
                    logger.warning("Сбой опроса событий: %s", poll_exc)
                    events = []
                if events:
                    writer.add_events(events)
                    await clear_events(context)
                if tick % 10 == 0:
                    logger.info(
                        "recording session=%s pages=%d frames=%d events_total=%d",
                        session_id,
                        len(context.pages),
                        sum(len(p.frames) for p in context.pages),
                        writer.event_count,
                    )
```

Дописать инициализацию в `_active_sessions[session_id]`:

```python
    _active_sessions[session_id] = {
        "writer": writer,
        "page": None,
        "stop_event": stop_event,
        "status": "connecting",
        "error": None,
        "injection_report": None,
        "target_url": None,
    }
```

- [ ] **Step 2: Коммит**

```bash
git add apps/api/routers/campaign_recorder.py
git commit -m "feat(recorder): wire InjectionReport into session runner with cycle logging"
```

---

### Task 5: Расширить /status новыми полями

**Files:**
- Modify: `apps/api/schemas.py`
- Modify: `apps/api/routers/campaign_recorder.py`

- [ ] **Step 1: Обновить RecorderStatusResponseSchema**

В `apps/api/schemas.py` найти `RecorderStatusResponseSchema` и добавить поля:

```python
class RecorderStatusResponseSchema(BaseModel):
    session_id: str
    status: str
    event_count: int
    error: str | None
    recent_events: list[RecorderEventSchema]
    injection_ok: bool = False
    target_url: str | None = None
    pages_injected: int = 0
```

- [ ] **Step 2: Заполнять новые поля в endpoint**

В `apps/api/routers/campaign_recorder.py` в `get_session_status`:

```python
    report = entry.get("injection_report")
    injection_ok = bool(report and report.ok)
    pages_injected = report.pages_injected if report else 0
    return RecorderStatusResponseSchema(
        session_id=session_id,
        status=entry.get("status", "unknown"),
        event_count=writer.event_count,
        error=entry.get("error"),
        recent_events=events_payload,
        injection_ok=injection_ok,
        target_url=entry.get("target_url"),
        pages_injected=pages_injected,
    )
```

- [ ] **Step 3: Коммит**

```bash
git add apps/api/schemas.py apps/api/routers/campaign_recorder.py
git commit -m "feat(recorder): expose injection_ok/target_url/pages_injected in status"
```

---

## Block 2 — Extended Event Capture

### Task 6: Расширенные поля события в JS

**Files:**
- Modify: `core/campaign_recorder/event_injector.py`
- Test: `tests/unit/test_campaign_recorder.py`

- [ ] **Step 1: Падающий тест — JS содержит сборку selector_candidates**

В `tests/unit/test_campaign_recorder.py` добавить:

```python
def test_injector_js_collects_extended_fields():
    """JS должен собирать label_text, placeholder, nearest_heading, selector_candidates."""
    from core.campaign_recorder.event_injector import BUILD_JS_INJECTOR

    js = BUILD_JS_INJECTOR("sid")
    for fragment in [
        "label_text",
        "placeholder",
        "nearest_heading",
        "selector_candidates",
    ]:
        assert fragment in js, f"JS не содержит {fragment}"
```

- [ ] **Step 2: Запустить — упадёт**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_collects_extended_fields -v
```

Expected: FAIL.

- [ ] **Step 3: Добавить хелперы и поля в JS**

В функции `BUILD_JS_INJECTOR` внутри `record(...)`-замыкания добавить хелперы и расширить структуру события (внутри тела JS):

```javascript
    function getLabelText(el) {
      return safe(function() {
        if (!el) return null;
        if (el.id) {
          var lbl = document.querySelector('label[for="' + el.id + '"]');
          if (lbl) return (lbl.innerText || '').trim().slice(0, 200) || null;
        }
        var p = el.parentElement;
        for (var i = 0; i < 4 && p; i++, p = p.parentElement) {
          if (p.tagName && p.tagName.toLowerCase() === 'label') {
            return (p.innerText || '').trim().slice(0, 200) || null;
          }
        }
        return null;
      }, null);
    }

    function getNearestHeading(el) {
      return safe(function() {
        var node = el;
        for (var i = 0; i < 8 && node; i++) {
          var sib = node.previousElementSibling;
          while (sib) {
            if (sib.matches && (sib.matches('h1,h2,h3,h4') || sib.getAttribute('role') === 'heading')) {
              return (sib.innerText || '').trim().slice(0, 200) || null;
            }
            sib = sib.previousElementSibling;
          }
          node = node.parentElement;
          if (node && node.matches && (node.matches('h1,h2,h3,h4') || node.getAttribute('role') === 'heading')) {
            return (node.innerText || '').trim().slice(0, 200) || null;
          }
        }
        return null;
      }, null);
    }

    function cssEscape(s) {
      return String(s).replace(/(["\\\\])/g, '\\\\$1');
    }

    function getAccessibleName(el) {
      return safe(function() {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        if (el.id) {
          var lbl = document.querySelector('label[for="' + el.id + '"]');
          if (lbl) return (lbl.innerText || '').trim();
        }
        var t = (el.innerText || el.textContent || '').trim();
        return t || null;
      }, null);
    }

    function isStableClass(c) {
      if (!c) return false;
      // FB-рандом: x + 6+ алфа-цифровых символов
      return !/^x[a-z0-9]{6,}$/i.test(c);
    }

    function getSelectorCandidates(el) {
      return safe(function() {
        var cands = [];
        var role = el.getAttribute && el.getAttribute('role');
        var name = getAccessibleName(el);
        if (role && name && name.length <= 80) {
          cands.push('role=' + role + '[name="' + cssEscape(name) + '"]');
        }
        var aria = el.getAttribute && el.getAttribute('aria-label');
        if (aria) cands.push('[aria-label="' + cssEscape(aria) + '"]');
        var dataAttrs = ['data-testid', 'data-pagelet', 'data-surface'];
        for (var i = 0; i < dataAttrs.length; i++) {
          var v = el.getAttribute && el.getAttribute(dataAttrs[i]);
          if (v) cands.push('[' + dataAttrs[i] + '="' + cssEscape(v) + '"]');
        }
        var tag = el.tagName ? el.tagName.toLowerCase() : '';
        var txt = (el.innerText || el.textContent || '').trim();
        if (txt && txt.length <= 60 && (tag === 'button' || tag === 'a' || role === 'button')) {
          cands.push('text="' + cssEscape(txt) + '"');
        }
        var stableClasses = Array.from(el.classList || []).filter(isStableClass);
        if (stableClasses.length) {
          cands.push(tag + '.' + stableClasses.join('.'));
        }
        cands.push('xpath=' + getXPath(el));
        return cands;
      }, []);
    }
```

И в самом `record(...)`-объекте `ev` добавить:

```javascript
          label_text: getLabelText(el),
          placeholder: safe(function() { return el.placeholder || null; }, null),
          nearest_heading: getNearestHeading(el),
          selector_candidates: getSelectorCandidates(el),
```

- [ ] **Step 4: Тест зелёный**

```bash
pytest tests/unit/test_campaign_recorder.py::test_injector_js_collects_extended_fields -v
```

Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_recorder/event_injector.py tests/unit/test_campaign_recorder.py
git commit -m "feat(recorder): collect label/placeholder/heading + ranked selector_candidates in JS"
```

---

## Block 3 — Analyzer Rewrite + Markdown Report

### Task 7: UserAction dataclass + денойз

**Files:**
- Rewrite: `core/campaign_recorder/analyzer.py`
- Test: `tests/unit/test_analyzer_denoise.py` (new)

- [ ] **Step 1: Падающие тесты на денойз**

Создать `tests/unit/test_analyzer_denoise.py`:

```python
"""Тесты на свёртку сырых событий в UserAction."""

from core.campaign_recorder.analyzer import denoise


def _ev(type_, xpath, ts, value=None, selectors=None, text="", label=None, heading=None):
    return {
        "type": type_,
        "xpath": xpath,
        "ts": ts,
        "value": value,
        "selector_candidates": selectors or [],
        "text": text,
        "label_text": label,
        "aria_label": None,
        "nearest_heading": heading,
        "tag": "div",
    }


def test_pointerdown_mousedown_click_collapse_to_single_click():
    """pointerdown + mousedown + click на одном элементе в пределах 200мс → один click."""
    events = [
        _ev("pointerdown", "/x", 1.00, selectors=["s1"]),
        _ev("mousedown", "/x", 1.05, selectors=["s1"]),
        _ev("click", "/x", 1.10, selectors=["s1"], text="OK"),
    ]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "click"


def test_consecutive_input_collapses_to_single_fill():
    """Подряд input на одном поле → один fill с финальным значением."""
    events = [
        _ev("input", "/i", 2.0, value="a", selectors=["s2"]),
        _ev("input", "/i", 2.1, value="ab", selectors=["s2"]),
        _ev("input", "/i", 2.2, value="abc", selectors=["s2"]),
    ]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "fill"
    assert actions[0].value == "abc"


def test_change_on_select_becomes_select_action():
    """change на <select> → select."""
    events = [{**_ev("change", "/s", 3.0, value="opt1", selectors=["s3"]), "tag": "select"}]
    actions = denoise(events)
    assert len(actions) == 1
    assert actions[0].kind == "select"
    assert actions[0].value == "opt1"


def test_noise_click_without_selectors_and_text_dropped():
    """Клик без selector_candidates и без text — отбрасывается."""
    events = [_ev("click", "/n", 4.0, selectors=[], text="")]
    actions = denoise(events)
    assert actions == []
```

- [ ] **Step 2: Запустить — упадут все**

```bash
pytest tests/unit/test_analyzer_denoise.py -v
```

Expected: FAIL — нет `denoise` и `UserAction`.

- [ ] **Step 3: Переписать analyzer.py**

Полностью заменить `core/campaign_recorder/analyzer.py`:

```python
"""Денойз сырых событий записи → list[UserAction]."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ActionKind = Literal["click", "fill", "select", "key", "submit"]


@dataclass(frozen=True)
class UserAction:
    kind: ActionKind
    selectors: tuple[str, ...]
    value: str | None
    label: str | None
    section: str | None
    ts: float
    raw_indices: tuple[int, ...]


_CLICK_WINDOW_S = 0.2
_KEY_SUBMITS = {"Enter", "Escape", "Tab"}


def _label_for(event: dict) -> str | None:
    return (
        event.get("label_text")
        or event.get("aria_label")
        or (event.get("text") or None)
    )


def _selectors_for(event: dict) -> tuple[str, ...]:
    cands = event.get("selector_candidates") or []
    return tuple(str(s) for s in cands if s)


def denoise(events: list[dict]) -> list[UserAction]:
    """Свёртка сырых событий в значимые действия."""
    actions: list[UserAction] = []
    i = 0
    n = len(events)
    pending_fill: dict[str, list[int]] = {}

    def flush_fill(xpath: str) -> None:
        idxs = pending_fill.pop(xpath, [])
        if not idxs:
            return
        last = events[idxs[-1]]
        selectors = _selectors_for(last)
        actions.append(
            UserAction(
                kind="fill",
                selectors=selectors,
                value=None if last.get("value") is None else str(last["value"]),
                label=_label_for(last),
                section=last.get("nearest_heading"),
                ts=float(last.get("ts") or 0),
                raw_indices=tuple(idxs),
            )
        )

    while i < n:
        e = events[i]
        kind = e.get("type")
        xpath = e.get("xpath") or ""

        if kind in ("pointerdown", "mousedown", "click"):
            for xk in list(pending_fill.keys()):
                flush_fill(xk)
            group = [i]
            j = i + 1
            click_idx: int | None = i if kind == "click" else None
            while j < n:
                ne = events[j]
                if ne.get("xpath") != xpath:
                    break
                if (float(ne.get("ts") or 0) - float(e.get("ts") or 0)) > _CLICK_WINDOW_S:
                    break
                if ne.get("type") not in ("pointerdown", "mousedown", "click"):
                    break
                group.append(j)
                if ne.get("type") == "click":
                    click_idx = j
                j += 1
            if click_idx is not None:
                src = events[click_idx]
                selectors = _selectors_for(src)
                text = (src.get("text") or "").strip()
                if selectors or text:
                    actions.append(
                        UserAction(
                            kind="click",
                            selectors=selectors,
                            value=None,
                            label=_label_for(src),
                            section=src.get("nearest_heading"),
                            ts=float(src.get("ts") or 0),
                            raw_indices=tuple(group),
                        )
                    )
            i = j
            continue

        if kind == "input":
            pending_fill.setdefault(xpath, []).append(i)
            i += 1
            continue

        if kind == "change":
            tag = (e.get("tag") or "").lower()
            if tag == "select":
                for xk in list(pending_fill.keys()):
                    flush_fill(xk)
                actions.append(
                    UserAction(
                        kind="select",
                        selectors=_selectors_for(e),
                        value=None if e.get("value") is None else str(e["value"]),
                        label=_label_for(e),
                        section=e.get("nearest_heading"),
                        ts=float(e.get("ts") or 0),
                        raw_indices=(i,),
                    )
                )
            else:
                # change на input — финал для pending fill, если есть
                if xpath in pending_fill:
                    pending_fill[xpath].append(i)
                    flush_fill(xpath)
                else:
                    pending_fill.setdefault(xpath, []).append(i)
                    flush_fill(xpath)
            i += 1
            continue

        if kind == "keydown":
            key = e.get("value")
            # последнее ли это событие для xpath
            is_last = not any(
                ev.get("xpath") == xpath for ev in events[i + 1 :]
            )
            if key in _KEY_SUBMITS and is_last:
                for xk in list(pending_fill.keys()):
                    flush_fill(xk)
                actions.append(
                    UserAction(
                        kind="key",
                        selectors=_selectors_for(e),
                        value=str(key),
                        label=_label_for(e),
                        section=e.get("nearest_heading"),
                        ts=float(e.get("ts") or 0),
                        raw_indices=(i,),
                    )
                )
            i += 1
            continue

        if kind == "submit":
            for xk in list(pending_fill.keys()):
                flush_fill(xk)
            actions.append(
                UserAction(
                    kind="submit",
                    selectors=_selectors_for(e),
                    value=None,
                    label=_label_for(e),
                    section=e.get("nearest_heading"),
                    ts=float(e.get("ts") or 0),
                    raw_indices=(i,),
                )
            )
            i += 1
            continue

        i += 1

    for xk in list(pending_fill.keys()):
        flush_fill(xk)

    return actions


def analyze_session(session: dict) -> dict:
    events: list[dict] = session.get("events", [])
    actions = denoise(events)
    return {
        "offer_code": session.get("offer_code", ""),
        "raw_events_count": len(events),
        "actions_count": len(actions),
        "actions": [
            {
                "kind": a.kind,
                "selectors": list(a.selectors),
                "value": a.value,
                "label": a.label,
                "section": a.section,
                "ts": a.ts,
            }
            for a in actions
        ],
    }


def analyze_session_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return analyze_session(data)
```

- [ ] **Step 4: Запустить тесты денойза**

```bash
pytest tests/unit/test_analyzer_denoise.py -v
```

Expected: PASS все 4.

- [ ] **Step 5: Удалить устаревшие тесты analyzer'а**

В `tests/unit/test_campaign_recorder.py` удалить `test_analyzer_counts_event_types` и `test_analyzer_detects_stable_selectors` (они описывают старую структуру отчёта).

- [ ] **Step 6: Прогнать весь файл**

```bash
pytest tests/unit/test_campaign_recorder.py -v
```

Expected: PASS оставшиеся.

- [ ] **Step 7: Коммит**

```bash
git add core/campaign_recorder/analyzer.py tests/unit/test_analyzer_denoise.py tests/unit/test_campaign_recorder.py
git commit -m "feat(recorder): rewrite analyzer with denoise pipeline producing UserAction"
```

---

### Task 8: Markdown-отчёт

**Files:**
- Create: `core/campaign_recorder/markdown_report.py`
- Test: `tests/unit/test_markdown_report.py` (new)

- [ ] **Step 1: Падающий тест**

Создать `tests/unit/test_markdown_report.py`:

```python
"""Генерация markdown-отчёта по списку UserAction."""

from core.campaign_recorder.analyzer import UserAction
from core.campaign_recorder.markdown_report import build_markdown


def test_build_markdown_contains_steps_and_selectors():
    """Отчёт содержит шапку, шаги, ранжированные селекторы."""
    actions = [
        UserAction(
            kind="click",
            selectors=("role=button[name=\"Conversion Location\"]", "[aria-label=\"Conversion Location\"]"),
            value=None,
            label="Conversion Location",
            section="Where do you want to drive traffic?",
            ts=1.0,
            raw_indices=(0, 1, 2),
        ),
        UserAction(
            kind="fill",
            selectors=("[aria-label=\"Website URL\"]",),
            value="https://example.com/landing",
            label="Website URL",
            section=None,
            ts=2.0,
            raw_indices=(3,),
        ),
    ]
    session = {
        "offer_code": "KE_CR2",
        "started_at": "2026-05-13T14:22:00",
        "saved_at": "2026-05-13T14:24:14",
        "events": [{}] * 312,
    }
    md = build_markdown(session, actions)
    assert "KE_CR2" in md
    assert "Шаг 1" in md
    assert "Шаг 2" in md
    assert "Conversion Location" in md
    assert "Website URL" in md
    assert "https://example.com/landing" in md
    assert "role=button" in md
```

- [ ] **Step 2: Запустить — упадёт**

```bash
pytest tests/unit/test_markdown_report.py -v
```

Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализовать markdown_report.py**

Создать `core/campaign_recorder/markdown_report.py`:

```python
"""Генерация markdown-отчёта по результатам денойза."""

from __future__ import annotations

from datetime import datetime

from core.campaign_recorder.analyzer import UserAction


def _format_duration(started: str | None, saved: str | None) -> str:
    try:
        s = datetime.fromisoformat(started)
        e = datetime.fromisoformat(saved)
        delta = int((e - s).total_seconds())
        m, sec = divmod(delta, 60)
        return f"{m} мин {sec} сек"
    except Exception:
        return "—"


def _action_title(a: UserAction) -> str:
    if a.kind == "click":
        what = a.label or "(элемент без подписи)"
        return f"click — «{what}»"
    if a.kind == "fill":
        what = a.label or "(поле без подписи)"
        return f"fill — поле «{what}»"
    if a.kind == "select":
        return f"select — «{a.label or '(без подписи)'}»"
    if a.kind == "key":
        return f"key — {a.value}"
    if a.kind == "submit":
        return f"submit — «{a.label or '(форма)'}»"
    return a.kind


def build_markdown(session: dict, actions: list[UserAction]) -> str:
    offer = session.get("offer_code", "—")
    started = session.get("started_at")
    saved = session.get("saved_at")
    raw_count = len(session.get("events", []))
    duration = _format_duration(started, saved)

    header_date = ""
    if started:
        try:
            header_date = datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M")
        except Exception:
            header_date = started

    lines = [
        f"# Запись {offer} — {header_date} — {len(actions)} действий",
        "",
        f"Длительность: {duration}",
        f"Сырых событий: {raw_count} → действий: {len(actions)}",
        "",
        "---",
        "",
    ]

    for idx, a in enumerate(actions, start=1):
        lines.append(f"## Шаг {idx} — {_action_title(a)}")
        lines.append("")
        if a.label:
            lines.append(f"**Что:** «{a.label}»")
        if a.section:
            lines.append(f"**Секция:** {a.section}")
        if a.value is not None:
            lines.append(f"**Значение:** `{a.value}`")
        lines.append("")
        lines.append("Селекторы:")
        if a.selectors:
            for i, sel in enumerate(a.selectors, start=1):
                lines.append(f"{i}. `{sel}`")
        else:
            lines.append("_нет стабильных селекторов_")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: Тест зелёный**

```bash
pytest tests/unit/test_markdown_report.py -v
```

Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add core/campaign_recorder/markdown_report.py tests/unit/test_markdown_report.py
git commit -m "feat(recorder): markdown report builder for UserAction list"
```

---

### Task 9: Новый /analyze endpoint и схема

**Files:**
- Modify: `apps/api/schemas.py`
- Modify: `apps/api/routers/campaign_recorder.py`

- [ ] **Step 1: Обновить RecorderAnalyzeResponseSchema**

В `apps/api/schemas.py` заменить тело `RecorderAnalyzeResponseSchema`:

```python
class RecorderAnalyzeResponseSchema(BaseModel):
    json_path: str
    md_path: str
    markdown: str
    actions_count: int
    raw_events_count: int
```

- [ ] **Step 2: Переписать endpoint**

В `apps/api/routers/campaign_recorder.py` импортировать:

```python
from core.campaign_recorder.analyzer import analyze_session_file, denoise
from core.campaign_recorder.markdown_report import build_markdown
import json
```

И заменить `analyze_last_recording`:

```python
@router.get("/analyze", response_model=RecorderAnalyzeResponseSchema)
async def analyze_last_recording(offer_code: str | None = None):
    recordings_dir = _RECORDINGS_DIR

    def _find_files() -> list[Path]:
        if not recordings_dir.exists():
            return []
        return sorted(
            [
                f
                for f in recordings_dir.glob("*.json")
                if (not offer_code or offer_code.upper() in f.name.upper())
            ],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

    files = await asyncio.to_thread(_find_files)
    if not files:
        if not await asyncio.to_thread(recordings_dir.exists):
            raise HTTPException(status_code=404, detail="Папка recordings не найдена")
        raise HTTPException(status_code=404, detail="Нет файлов записи")

    json_path = files[0]

    def _build() -> dict:
        session = json.loads(json_path.read_text(encoding="utf-8"))
        actions = denoise(session.get("events", []))
        md = build_markdown(session, actions)
        md_path = json_path.with_suffix(".md")
        md_path.write_text(md, encoding="utf-8")
        return {
            "json_path": str(json_path),
            "md_path": str(md_path),
            "markdown": md,
            "actions_count": len(actions),
            "raw_events_count": len(session.get("events", [])),
        }

    payload = await asyncio.to_thread(_build)
    return RecorderAnalyzeResponseSchema(**payload)
```

- [ ] **Step 3: Прогнать unit-тесты**

```bash
pytest tests/unit/test_campaign_recorder.py tests/unit/test_analyzer_denoise.py tests/unit/test_markdown_report.py tests/unit/test_injection_report.py -v
```

Expected: всё зелёное.

- [ ] **Step 4: Коммит**

```bash
git add apps/api/schemas.py apps/api/routers/campaign_recorder.py
git commit -m "feat(recorder): new /analyze endpoint returns markdown + saves .md next to .json"
```

---

### Task 10: Frontend — индикатор инжекта и просмотр md

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/pages/ScriptsPage.jsx`

- [ ] **Step 1: api.js — пробросить новые поля**

В `frontend/src/api.js` найти `analyzeLastRecording`. Функция уже возвращает JSON-тело — менять не надо, но если есть преобразование под старую схему — удалить. Получить итог:

```javascript
export async function analyzeLastRecording(offerCode) {
  const params = offerCode ? `?offer_code=${encodeURIComponent(offerCode)}` : '';
  const res = await fetch(`${API_BASE}/api/campaign-recorder/analyze${params}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json(); // { json_path, md_path, markdown, actions_count, raw_events_count }
}
```

Аналогично `getRecordingStatus` — оставить как есть, поля `injection_ok/target_url/pages_injected` придут из бэка автоматически.

- [ ] **Step 2: ScriptsPage.jsx — индикатор и md-просмотр**

В `frontend/src/pages/ScriptsPage.jsx` в разделе рендера панели рекордера:

1. Добавить индикатор инжекта рядом со статусом записи:

```jsx
{recorderStatus && (
  <div className="recorder-injection">
    <span className={recorderStatus.injection_ok ? 'ok' : 'bad'}>
      {recorderStatus.injection_ok ? '● инжект OK' : '● инжект НЕТ'}
    </span>
    {recorderStatus.target_url && (
      <span className="target-url">{recorderStatus.target_url}</span>
    )}
    {recorderStatus.pages_injected != null && (
      <span>страниц: {recorderStatus.pages_injected}</span>
    )}
  </div>
)}
```

2. После кнопки «Проанализировать» добавить блок с markdown:

```jsx
{recorderReport?.markdown && (
  <div className="recorder-md">
    <div className="recorder-md-toolbar">
      <span>Действий: {recorderReport.actions_count} (из {recorderReport.raw_events_count} событий)</span>
      <a
        href={`data:text/markdown;charset=utf-8,${encodeURIComponent(recorderReport.markdown)}`}
        download={(recorderReport.md_path || 'report.md').split('/').pop()}
      >
        Скачать .md
      </a>
    </div>
    <pre className="recorder-md-pre">{recorderReport.markdown}</pre>
  </div>
)}
```

3. Если в файле есть отрисовка старых полей `stable_selectors` / `fragile_selectors` / `recommendations` — удалить эти секции.

- [ ] **Step 3: Проверить сборку фронта**

```bash
cd frontend && npm run build
```

Expected: успешная сборка без ошибок.

- [ ] **Step 4: Коммит**

```bash
git add frontend/src/api.js frontend/src/pages/ScriptsPage.jsx
git commit -m "feat(recorder): UI shows injection status + renders markdown report"
```

---

## Final Verification

- [ ] **Step 1: Полный прогон unit-тестов**

```bash
pytest tests/unit/test_campaign_recorder.py tests/unit/test_analyzer_denoise.py tests/unit/test_markdown_report.py tests/unit/test_injection_report.py -v
```

Expected: PASS всё.

- [ ] **Step 2: Линт**

```bash
ruff check core/campaign_recorder apps/api/routers/campaign_recorder.py apps/api/schemas.py
ruff format --check core/campaign_recorder apps/api/routers/campaign_recorder.py
```

Expected: clean.

- [ ] **Step 3: Ручная проверка живьём (только если Vision доступен)**

```bash
./run.sh
```

В UI: открыть Scripts → ввести offer_code → «Старт записи» → проверить что в `/status` `injection_ok: true` и `target_url` — Ads Manager → сделать действия → «Стоп» → «Проанализировать» → markdown появился, шаги читаются.

---

## Self-Review Checklist

- ✅ **Spec coverage:**
  - Блок 1 (диагностика) → Tasks 1, 2, 3, 4, 5
  - Блок 2 (расширение события) → Task 6
  - Блок 3 (analyzer + markdown + API + UI) → Tasks 7, 8, 9, 10
- ✅ **No placeholders:** все шаги имеют код или точные команды.
- ✅ **Type consistency:** `UserAction` определён в Task 7, используется в Tasks 8, 9; `InjectionReport` — Task 3, используется в 4, 5; `BUILD_JS_INJECTOR(session_id)` — Task 2, используется в 3, 6.
- ✅ Тесты для всех новых модулей: `test_injection_report.py`, `test_analyzer_denoise.py`, `test_markdown_report.py`, обновление `test_campaign_recorder.py`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-recorder-fix-and-analyzer.md`. Two execution options:

1. **Subagent-Driven (recommended)** — я диспатчу свежего сабагента на каждую таску, ревью между тасками, быстрая итерация.
2. **Inline Execution** — выполняю таски прямо в этой сессии (executing-plans), батчем с чекпойнтами.

Какой подход?
