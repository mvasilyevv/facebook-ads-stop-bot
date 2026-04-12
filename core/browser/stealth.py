# -*- coding: utf-8 -*-
"""Stealth-патчи для Playwright: скрывают автоматизацию от детекторов.

Заменяет удалённый пакет rebrowser-patches.
Все патчи применяются через page.add_init_script — работают до загрузки JS страницы.
"""

from __future__ import annotations

import logging

from patchright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)

# Скрипт инициализации — применяется ко всем новым страницам.
# Скрывает признаки автоматизации Chrome/Playwright.
_STEALTH_INIT_SCRIPT = """
() => {
    // 1. navigator.webdriver — главный маркер автоматизации
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });

    // 2. chrome.runtime — отсутствует в headless/automated Chrome
    const originalRuntime = window.chrome && window.chrome.runtime;
    if (!originalRuntime) {
        Object.defineProperty(window, 'chrome', {
            value: Object.assign({}, window.chrome || {}, {
                runtime: {
                    OnInstalledReason: {
                        CHROME_UPDATE: 'chrome_update',
                        INSTALL: 'install',
                        SHARED_MODULE_UPDATE: 'shared_module_update',
                        UPDATE: 'update',
                    },
                },
            }),
            writable: false,
            enumerable: true,
            configurable: false,
        });
    }

    // 3. Permissions API — query для notifications должен возвращать 'prompt'
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {
        window.navigator.permissions.query = (parameters) => {
            return parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission, name: 'notifications' })
                : originalQuery.call(window.navigator.permissions, parameters);
        };
        Object.defineProperty(window.navigator.permissions, 'query', { enumerable: true });
    }

    // 4. Plugins — пустой массив плагинов выдаёт headless
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ],
    });

    // 5. Languages — стандартные для Chrome
    Object.defineProperty(navigator, 'languages', {
        get: () => ['ru-RU', 'ru', 'en-US', 'en'],
    });

    // 6. WebGL Vendor/Renderer — не должны быть "Google Inc. (Google)/SwiftShader"
    const getParameterProxyHandler = {
        apply(target, thisArg, args) {
            const param = args[0];
            const result = Reflect.apply(target, thisArg, args);
            // UNMASKED_VENDOR_WEBGL
            if (param === 37445) return 'Intel Inc.';
            // UNMASKED_RENDERER_WEBGL
            if (param === 37446) return 'Intel Iris OpenGL Engine';
            return result;
        },
    };

    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl && gl.getParameter) {
            gl.getParameter = new Proxy(gl.getParameter, getParameterProxyHandler);
        }
    } catch (_) {}

    // 7. iframe.contentWindow — фикс для баг-детекта headless
    const originalDescriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (originalDescriptor && originalDescriptor.get) {
        const originalGet = originalDescriptor.get;
        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
            ...originalDescriptor,
            get: function() {
                const win = originalGet.call(this);
                if (win && win.navigator && win.navigator.webdriver !== undefined) {
                    Object.defineProperty(win.navigator, 'webdriver', {
                        get: () => undefined,
                    });
                }
                return win;
            },
        });
    }

    // 8. Connection — стандартный тип для десктопа
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            downlink: 10,
            effectiveType: '4g',
            rtt: 50,
            saveData: false,
        }),
    });

    // 9. Device memory и hardware concurrency
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });
}
"""


async def apply_stealth_to_page(page: Page) -> None:
    """Применяет stealth-скрипт к одной странице.

    Используется как fallback если контекст уже создан без init_script.
    """
    try:
        await page.add_init_script(_STEALTH_INIT_SCRIPT)
    except Exception:
        logger.debug("Не удалось применить stealth-скрипт к странице", exc_info=True)


def patch_patchright() -> None:
    """Патчит BrowserContext.new_page для автоматического stealth.

    Drop-in замена для `from rebrowser_patches import patch_patchright`.
    После вызова все новые страницы автоматически получают stealth-скрипт.
    """
    from patchright.async_api import BrowserContext as _BrowserContext

    original_new_page = _BrowserContext.new_page

    async def _stealth_new_page(self: "BrowserContext", *args, **kwargs) -> "Page":
        page = await original_new_page(self, *args, **kwargs)
        try:
            await page.add_init_script(_STEALTH_INIT_SCRIPT)
        except Exception:
            logger.debug("Не удалось применить stealth-скрипт при создании страницы", exc_info=True)
        return page

    _BrowserContext.new_page = _stealth_new_page
    logger.info("Stealth-патчи применены к patchright")
