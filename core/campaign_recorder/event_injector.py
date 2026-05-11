from __future__ import annotations

import json  # noqa: F401
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)
_MAX_TEXT_LEN = 200


def BUILD_JS_INJECTOR() -> str:
    """Возвращает JS-сниппет, который собирает события DOM."""
    return f"""
(function() {{
  if (window.__fbRecorder) return;
  window.__fbRecorder = {{ events: [] }};

  function getXPath(el) {{
    if (!el) return '';
    if (el === document.body) return '/html/body';
    const idx = Array.from(el.parentNode.children).indexOf(el) + 1;
    return getXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + idx + ']';
  }}

  function getDataAttrs(el) {{
    const result = {{}};
    Array.from(el.attributes).forEach(function(attr) {{
      if (attr.name.startsWith('data-')) result[attr.name] = attr.value;
    }});
    return result;
  }}

  function getText(el) {{
    const t = (el.innerText || el.textContent || '').trim();
    return t.length > {_MAX_TEXT_LEN} ? t.slice(0, {_MAX_TEXT_LEN}) : t;
  }}

  function record(type, el, value) {{
    const ev = {{
      ts: Date.now() / 1000,
      type: type,
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      id: el.id || '',
      classes: Array.from(el.classList || []),
      data_attrs: getDataAttrs(el),
      xpath: getXPath(el),
      text: getText(el),
      value: value !== undefined ? value : null,
      x: el.getBoundingClientRect ? Math.round(el.getBoundingClientRect().x) : 0,
      y: el.getBoundingClientRect ? Math.round(el.getBoundingClientRect().y) : 0,
      role: el.getAttribute ? el.getAttribute('role') : null,
      aria_label: el.getAttribute ? el.getAttribute('aria-label') : null
    }};
    window.__fbRecorder.events.push(ev);
  }}

  document.addEventListener('click', function(e) {{ record('click', e.target); }}, true);
  document.addEventListener('input', function(e) {{ record('input', e.target, e.target.value); }}, true);
  document.addEventListener('change', function(e) {{ record('change', e.target, e.target.value); }}, true);
  document.addEventListener('select', function(e) {{ record('select', e.target, e.target.value); }}, true);
  document.addEventListener('focus', function(e) {{ record('focus', e.target); }}, true);
}})();
"""


async def inject_event_listener(page: Page) -> None:
    """Инжектирует JS-слушатели в текущую страницу."""
    js = BUILD_JS_INJECTOR()
    await page.evaluate(js)
    logger.info("JS-слушатели инжектированы в страницу")


async def collect_events(page: Page) -> list[dict]:
    """Собирает накопленные события из window.__fbRecorder."""
    result = await page.evaluate(
        "() => window.__fbRecorder ? window.__fbRecorder.events : []"
    )
    return result if isinstance(result, list) else []


async def clear_events(page: Page) -> None:
    """Сбрасывает накопленные события."""
    await page.evaluate(
        "() => { if (window.__fbRecorder) window.__fbRecorder.events = []; }"
    )
