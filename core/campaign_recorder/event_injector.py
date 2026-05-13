from __future__ import annotations

import logging
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Frame, Page

logger = logging.getLogger(__name__)
_MAX_TEXT_LEN = 200


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


def BUILD_JS_INJECTOR(session_id: str = "") -> str:
    """Возвращает JS-сниппет, который собирает события DOM."""
    js_sid = repr(session_id)
    return f"""
(function() {{
  try {{
    var SESSION_ID = {js_sid};
    if (window.__fbRecorder && window.__fbRecorder.installed && window.__fbRecorder.session_id === SESSION_ID) return;
    window.__fbRecorder = window.__fbRecorder || {{ events: [] }};
    window.__fbRecorder.installed = true;
    window.__fbRecorder.session_id = SESSION_ID;

    function safe(fn, fallback) {{
      try {{ return fn(); }} catch (e) {{ return fallback; }}
    }}

    function getXPath(el) {{
      return safe(function() {{
        if (!el || el.nodeType !== 1) return '';
        if (el === document.body) return '/html/body';
        if (!el.parentNode || !el.parentNode.children) return '';
        var idx = Array.from(el.parentNode.children).indexOf(el) + 1;
        return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + idx + ']';
      }}, '');
    }}

    function getDataAttrs(el) {{
      return safe(function() {{
        var result = {{}};
        if (!el || !el.attributes) return result;
        Array.from(el.attributes).forEach(function(attr) {{
          if (attr.name.startsWith('data-')) result[attr.name] = attr.value;
        }});
        return result;
      }}, {{}});
    }}

    function getText(el) {{
      return safe(function() {{
        if (!el) return '';
        var t = (el.innerText || el.textContent || '').trim();
        return t.length > {_MAX_TEXT_LEN} ? t.slice(0, {_MAX_TEXT_LEN}) : t;
      }}, '');
    }}

    function record(type, target, value) {{
      try {{
        var el = (target && target.nodeType === 1) ? target
                : (target && target.parentElement) ? target.parentElement
                : null;
        if (!el) return;
        var rect = safe(function() {{ return el.getBoundingClientRect(); }}, {{x: 0, y: 0}});
        var ev = {{
          ts: Date.now() / 1000,
          type: type,
          tag: el.tagName ? el.tagName.toLowerCase() : '',
          id: el.id || '',
          classes: safe(function() {{ return Array.from(el.classList || []); }}, []),
          data_attrs: getDataAttrs(el),
          xpath: getXPath(el),
          text: getText(el),
          value: value !== undefined ? value : null,
          x: Math.round(rect.x || 0),
          y: Math.round(rect.y || 0),
          role: safe(function() {{ return el.getAttribute && el.getAttribute('role'); }}, null),
          aria_label: safe(function() {{ return el.getAttribute && el.getAttribute('aria-label'); }}, null),
          url: location ? location.href : ''
        }};
        window.__fbRecorder.events.push(ev);
      }} catch (e) {{
        // молча игнорируем — не ломаем браузер пользователя
      }}
    }}

    function pickTarget(e) {{
      // Для shadow DOM e.target — это host. composedPath даёт реальный элемент.
      try {{
        if (typeof e.composedPath === 'function') {{
          var path = e.composedPath();
          for (var i = 0; i < path.length; i++) {{
            var n = path[i];
            if (n && n.nodeType === 1) return n;
          }}
        }}
      }} catch (err) {{}}
      return e.target;
    }}

    document.addEventListener('pointerdown', function(e) {{ record('pointerdown', pickTarget(e)); }}, true);
    document.addEventListener('mousedown',   function(e) {{ record('mousedown',   pickTarget(e)); }}, true);
    document.addEventListener('click',  function(e) {{ record('click',  pickTarget(e)); }}, true);
    document.addEventListener('input',  function(e) {{ var t = pickTarget(e); record('input',  t, t && t.value); }}, true);
    document.addEventListener('change', function(e) {{ var t = pickTarget(e); record('change', t, t && t.value); }}, true);
    document.addEventListener('keydown',function(e) {{
      if (e.key === 'Enter' || e.key === 'Tab' || e.key === 'Escape' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {{
        record('keydown', pickTarget(e), e.key);
      }}
    }}, true);
    document.addEventListener('submit', function(e) {{ record('submit', pickTarget(e)); }}, true);
  }} catch (e) {{
    /* swallow */
  }}
}})();
"""


async def _inject_into_frame(frame: Frame, session_id: str = "") -> None:
    """Инжектирует слушатели в один фрейм. Не падает при ошибках."""
    try:
        await frame.evaluate(BUILD_JS_INJECTOR(session_id))
    except Exception as exc:
        logger.debug("Не удалось инжектить в фрейм %s: %s", getattr(frame, "url", "?"), exc)


async def inject_into_page(page: Page, session_id: str = "") -> None:
    """Инжектирует слушатели во все фреймы страницы."""
    for frame in page.frames:
        await _inject_into_frame(frame, session_id)


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


async def _build_injection_report(context: BrowserContext, session_id: str) -> InjectionReport:
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


async def attach_recorder(context: BrowserContext, session_id: str = "") -> InjectionReport:
    """Подключает запись ко всему контексту: текущим страницам, новым страницам и фреймам.

    1. init_script — сработает на новых страницах/фреймах автоматически.
    2. Текущие страницы инжектим вручную (init_script на них не действует).
    3. Слушаем page/framenavigated — заново инжектим после SPA-навигаций.
    """
    js = BUILD_JS_INJECTOR(session_id)
    try:
        await context.add_init_script(js)
        logger.info("init_script зарегистрирован для контекста")
    except Exception as exc:
        logger.warning("Не удалось зарегистрировать init_script: %s", exc)

    for page in context.pages:
        await inject_into_page(page, session_id)

    def _on_new_page(page: Page) -> None:
        logger.info("Новая вкладка: %s", page.url)

        async def _setup():
            await inject_into_page(page, session_id)

        try:
            page.on(
                "framenavigated",
                lambda fr: _safe_create_task(_inject_into_frame(fr, session_id)),
            )
        except Exception:
            pass
        _safe_create_task(_setup())

    context.on("page", _on_new_page)

    for page in context.pages:
        page.on(
            "framenavigated",
            lambda fr: _safe_create_task(_inject_into_frame(fr, session_id)),
        )

    logger.info(
        "Recorder подключён. Страниц в контексте: %d, фреймов суммарно: %d",
        len(context.pages),
        sum(len(p.frames) for p in context.pages),
    )

    return await _build_injection_report(context, session_id)


def _safe_create_task(coro) -> None:
    import asyncio

    try:
        asyncio.get_event_loop().create_task(coro)
    except Exception:
        # на закрытом loop — просто игнорируем
        pass


async def inject_event_listener(page: Page, session_id: str = "") -> None:
    """Старая совместимая обёртка — инжект в одну страницу."""
    await inject_into_page(page, session_id)


async def collect_events(page_or_context) -> list[dict]:
    """Собирает события со всех страниц контекста (или с одной страницы)."""
    if hasattr(page_or_context, "pages"):
        ctx: BrowserContext = page_or_context
        result: list[dict] = []
        for page in ctx.pages:
            for frame in page.frames:
                try:
                    chunk = await frame.evaluate(
                        "() => window.__fbRecorder ? window.__fbRecorder.events : []"
                    )
                    if isinstance(chunk, list) and chunk:
                        result.extend(chunk)
                except Exception:
                    continue
        return result
    page: Page = page_or_context
    result = []
    for frame in page.frames:
        try:
            chunk = await frame.evaluate(
                "() => window.__fbRecorder ? window.__fbRecorder.events : []"
            )
            if isinstance(chunk, list) and chunk:
                result.extend(chunk)
        except Exception:
            continue
    return result


async def clear_events(page_or_context) -> None:
    """Сбрасывает накопленные события во всех фреймах."""
    if hasattr(page_or_context, "pages"):
        ctx: BrowserContext = page_or_context
        for page in ctx.pages:
            for frame in page.frames:
                try:
                    await frame.evaluate(
                        "() => { if (window.__fbRecorder) window.__fbRecorder.events = []; }"
                    )
                except Exception:
                    continue
        return
    page: Page = page_or_context
    for frame in page.frames:
        try:
            await frame.evaluate(
                "() => { if (window.__fbRecorder) window.__fbRecorder.events = []; }"
            )
        except Exception:
            continue
