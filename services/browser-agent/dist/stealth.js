"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.STEALTH_INIT_SCRIPT = void 0;
/** Скрипт маскировки автоматизации, который применяется через context.addInitScript() ко всем страницам.
 *
 * Скрытые признаки автоматизации:
 * 1. navigator.webdriver = undefined
 * 2. chrome.runtime — фейковый
 * 3. permissions.query — notifications = 'prompt'
 * 4. navigator.plugins — 3 фейковых плагина
 * 5. navigator.languages = ['ru-RU', 'ru', 'en-US', 'en']
 * 6. WebGL vendor/renderer = Intel Inc. / Intel Iris OpenGL Engine
 * 7. iframe.contentWindow — фикс webdriver
 * 8. navigator.connection = 4g desktop
 * 9. deviceMemory = 8, hardwareConcurrency = 8
 */
exports.STEALTH_INIT_SCRIPT = `
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
            // Константа WebGL для настоящего поставщика видеодрайвера.
            if (param === 37445) return 'Intel Inc.';
            // Константа WebGL для настоящего рендера видеодрайвера.
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
`;
//# sourceMappingURL=stealth.js.map