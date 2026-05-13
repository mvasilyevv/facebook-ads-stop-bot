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
    // Снимаем слушатели предыдущей инъекции, иначе старый код продолжит писать события в параллель.
    if (window.__fbRecorder && window.__fbRecorder.listeners) {{
      try {{
        window.__fbRecorder.listeners.forEach(function(l) {{
          document.removeEventListener(l.type, l.fn, l.capture);
        }});
      }} catch (e) {{}}
    }}
    window.__fbRecorder = window.__fbRecorder || {{ events: [] }};
    window.__fbRecorder.installed = true;
    window.__fbRecorder.session_id = SESSION_ID;
    window.__fbRecorder.listeners = [];

    function on(type, fn, capture) {{
      document.addEventListener(type, fn, capture);
      window.__fbRecorder.listeners.push({{ type: type, fn: fn, capture: !!capture }});
    }}

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

    function shortXPath(el) {{
      // Последние 6 сегментов — для случаев, когда нет нормального селектора.
      var full = getXPath(el);
      if (!full) return '';
      var parts = full.split('/').filter(Boolean);
      if (parts.length <= 6) return '/' + parts.join('/');
      return '/.../' + parts.slice(-6).join('/');
    }}

    var INTERACTIVE_TAGS = {{button:1,a:1,input:1,select:1,textarea:1,label:1,summary:1}};
    var INTERACTIVE_ROLES = {{button:1,link:1,option:1,menuitem:1,menuitemcheckbox:1,menuitemradio:1,
                              tab:1,switch:1,checkbox:1,radio:1,combobox:1,listbox:1,searchbox:1,
                              textbox:1,slider:1,treeitem:1}};

    function isInteractive(el) {{
      if (!el || el.nodeType !== 1) return false;
      var tag = el.tagName ? el.tagName.toLowerCase() : '';
      if (INTERACTIVE_TAGS[tag]) return true;
      var role = el.getAttribute && el.getAttribute('role');
      if (role && INTERACTIVE_ROLES[role]) return true;
      if (el.getAttribute && el.getAttribute('tabindex') !== null) return true;
      if (el.onclick) return true;
      // FB-специфика: data-auto-logging-id висит на семантически кликабельных див-кнопках.
      if (el.getAttribute && el.getAttribute('data-auto-logging-id')) return true;
      return false;
    }}

    function isPointerCursor(el) {{
      return safe(function() {{
        return el && el.nodeType === 1 && getComputedStyle(el).cursor === 'pointer';
      }}, false);
    }}

    function findInteractiveAncestor(el) {{
      // Сначала ищем «настоящий» интерактивный предок до 12 уровней — это сильный сигнал.
      var node = el;
      for (var i = 0; i < 12 && node; i++) {{
        if (isInteractive(node)) return node;
        node = node.parentElement;
      }}
      // Запасной путь: подняться по цепочке cursor:pointer до последнего такого узла.
      // Так мы попадём на «корень» кликабельной зоны (FB любит вкладывать pointer-див в pointer-див).
      if (!isPointerCursor(el)) return el;
      var last = el;
      node = el.parentElement;
      for (var j = 0; j < 12 && node; j++) {{
        if (!isPointerCursor(node)) break;
        last = node;
        node = node.parentElement;
      }}
      return last;
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

    function getLabelText(el) {{
      return safe(function() {{
        if (!el) return null;
        if (el.id) {{
          var lbl = document.querySelector('label[for="' + el.id + '"]');
          if (lbl) return (lbl.innerText || '').trim().slice(0, 200) || null;
        }}
        var p = el.parentElement;
        for (var i = 0; i < 4 && p; i++, p = p.parentElement) {{
          if (p.tagName && p.tagName.toLowerCase() === 'label') {{
            return (p.innerText || '').trim().slice(0, 200) || null;
          }}
        }}
        return null;
      }}, null);
    }}

    function getNearestHeading(el) {{
      return safe(function() {{
        var node = el;
        for (var i = 0; i < 8 && node; i++) {{
          var sib = node.previousElementSibling;
          while (sib) {{
            if (sib.matches && (sib.matches('h1,h2,h3,h4') || sib.getAttribute('role') === 'heading')) {{
              return (sib.innerText || '').trim().slice(0, 200) || null;
            }}
            sib = sib.previousElementSibling;
          }}
          node = node.parentElement;
          if (node && node.matches && (node.matches('h1,h2,h3,h4') || node.getAttribute('role') === 'heading')) {{
            return (node.innerText || '').trim().slice(0, 200) || null;
          }}
        }}
        return null;
      }}, null);
    }}

    function cssEscape(s) {{
      return String(s).replace(/(["\\\\])/g, '\\\\$1');
    }}

    function getAccessibleName(el) {{
      return safe(function() {{
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        var labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {{
          var ref = document.getElementById(labelledBy);
          if (ref) return (ref.innerText || '').trim();
        }}
        if (el.id) {{
          var lbl = document.querySelector('label[for="' + el.id + '"]');
          if (lbl) return (lbl.innerText || '').trim();
        }}
        var t = (el.innerText || el.textContent || '').trim();
        // Только короткий текст идёт в имя — длинные простыни это контейнер, не кнопка.
        if (t && t.length <= 80 && t.indexOf('\\n') === -1) return t;
        return null;
      }}, null);
    }}

    function isStableClass(c) {{
      if (!c) return false;
      return !/^x[a-z0-9]{{6,}}$/i.test(c)
          && !/^_[a-z0-9]{{4,}}$/i.test(c)
          && !/^[a-z]{{1,3}}[0-9a-f]{{6,}}$/i.test(c);
    }}

    function findScopeAttr(el) {{
      // Ищем стабильный data-* атрибут на элементе или у предков (≤6 уровней).
      var node = el;
      for (var i = 0; i < 6 && node; i++) {{
        if (node.getAttribute) {{
          var keys = ['data-testid', 'data-pagelet', 'data-surface', 'data-auto-logging-id'];
          for (var k = 0; k < keys.length; k++) {{
            var v = node.getAttribute(keys[k]);
            if (v) return {{ key: keys[k], value: v, isSelf: i === 0 }};
          }}
        }}
        node = node.parentElement;
      }}
      return null;
    }}

    function effectiveRole(el) {{
      var role = el.getAttribute && el.getAttribute('role');
      if (role) return role;
      var tag = el.tagName ? el.tagName.toLowerCase() : '';
      if (tag === 'button') return 'button';
      if (tag === 'a' && el.getAttribute && el.getAttribute('href')) return 'link';
      if (tag === 'input') {{
        var type = (el.getAttribute('type') || 'text').toLowerCase();
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'button' || type === 'submit') return 'button';
        return 'textbox';
      }}
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      return null;
    }}

    function getSelectorCandidates(el) {{
      return safe(function() {{
        var cands = [];
        var role = effectiveRole(el);
        var name = getAccessibleName(el);
        var tag = el.tagName ? el.tagName.toLowerCase() : '';
        var txt = (el.innerText || el.textContent || '').trim();
        var shortTxt = (txt && txt.length <= 60 && txt.indexOf('\\n') === -1) ? txt : null;

        if (role && name && name.length <= 80) {{
          cands.push('role=' + role + '[name="' + cssEscape(name) + '"]');
        }}

        var aria = el.getAttribute && el.getAttribute('aria-label');
        if (aria) cands.push('[aria-label="' + cssEscape(aria) + '"]');

        var placeholder = el.getAttribute && el.getAttribute('placeholder');
        if (placeholder) cands.push('[placeholder="' + cssEscape(placeholder) + '"]');

        var dataAttrs = ['data-testid', 'data-pagelet', 'data-surface', 'data-auto-logging-id'];
        for (var i = 0; i < dataAttrs.length; i++) {{
          var v = el.getAttribute && el.getAttribute(dataAttrs[i]);
          if (v) cands.push('[' + dataAttrs[i] + '="' + cssEscape(v) + '"]');
        }}

        // Scope от предка с data-* + текст/role — спасает «голые» div со текстом.
        var scope = findScopeAttr(el);
        if (scope && !scope.isSelf) {{
          var scopeSel = '[' + scope.key + '="' + cssEscape(scope.value) + '"]';
          if (role && name && name.length <= 80) {{
            cands.push(scopeSel + ' >> role=' + role + '[name="' + cssEscape(name) + '"]');
          }} else if (shortTxt) {{
            cands.push(scopeSel + ' >> text="' + cssEscape(shortTxt) + '"');
          }}
        }}

        // text="..." — даём для любого элемента с коротким однострочным текстом, не только кликабельных.
        // FB часто использует <div>Сайт</div> как кнопку без role/onclick.
        if (shortTxt) {{
          cands.push('text="' + cssEscape(shortTxt) + '"');
        }}

        var stableClasses = Array.from(el.classList || []).filter(isStableClass);
        if (stableClasses.length) {{
          cands.push(tag + '.' + stableClasses.join('.'));
        }}

        // xpath даём только если ничего лучше нет, и в короткой форме
        if (cands.length === 0) {{
          cands.push('xpath=' + getXPath(el));
        }} else {{
          cands.push('xpath=' + shortXPath(el));
        }}
        return cands;
      }}, []);
    }}

    function record(type, target, value) {{
      try {{
        var raw = (target && target.nodeType === 1) ? target
                : (target && target.parentElement) ? target.parentElement
                : null;
        if (!raw) return;
        // Для click/pointerdown/mousedown поднимаемся до интерактивного предка.
        // Для input/change/keydown оставляем сам элемент — нужен реальный инпут.
        var liftTypes = {{click: 1, pointerdown: 1, mousedown: 1}};
        var el = liftTypes[type] ? findInteractiveAncestor(raw) : raw;
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
          label_text: getLabelText(el),
          placeholder: safe(function() {{ return el.placeholder || null; }}, null),
          nearest_heading: getNearestHeading(el),
          selector_candidates: getSelectorCandidates(el),
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

    on('pointerdown', function(e) {{ record('pointerdown', pickTarget(e)); }}, true);
    on('mousedown',   function(e) {{ record('mousedown',   pickTarget(e)); }}, true);
    on('click',  function(e) {{ record('click',  pickTarget(e)); }}, true);
    on('input',  function(e) {{ var t = pickTarget(e); record('input',  t, t && t.value); }}, true);
    on('change', function(e) {{ var t = pickTarget(e); record('change', t, t && t.value); }}, true);
    on('keydown',function(e) {{
      if (e.key === 'Enter' || e.key === 'Tab' || e.key === 'Escape' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {{
        record('keydown', pickTarget(e), e.key);
      }}
    }}, true);
    on('submit', function(e) {{ record('submit', pickTarget(e)); }}, true);
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
