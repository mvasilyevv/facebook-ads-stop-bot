"use strict";
// Проверка hardReloadPage: вызывает clearBrowserCache через CDPSession и затем page.reload({ waitUntil: 'networkidle' }), возвращая длительность.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const hard_reload_js_1 = require("./hard-reload.js");
function mockPage(overrides = {}) {
    const cdpSend = overrides.cdpSend ?? ((..._args) => Promise.resolve());
    const detach = overrides.detach ?? (() => Promise.resolve());
    const newCDPSession = overrides.newCDPSession ?? (() => Promise.resolve({ send: cdpSend, detach }));
    const reload = overrides.reload ?? (() => Promise.resolve());
    const calls = { cdpSend: [], reload: [], newCDPSessionCalled: 0, detachCalled: 0 };
    const trackedCdpSend = (...args) => { calls.cdpSend.push(args); return cdpSend(...args); };
    const trackedDetach = () => { calls.detachCalled += 1; return detach(); };
    const trackedNewCDPSession = (page) => {
        calls.newCDPSessionCalled += 1;
        return Promise.resolve({ send: trackedCdpSend, detach: trackedDetach });
    };
    const trackedReload = (...args) => { calls.reload.push(args); return reload(...args); };
    const page = {
        context: () => ({ newCDPSession: trackedNewCDPSession }),
        reload: trackedReload,
    };
    return { page, calls };
}
(0, node_test_1.describe)('hardReloadPage', () => {
    (0, node_test_1.it)('очищает кеш через CDP и перезагружает страницу при bypassCache=true', async () => {
        const { page, calls } = mockPage();
        const result = await (0, hard_reload_js_1.hardReloadPage)(page, true);
        strict_1.default.equal(calls.newCDPSessionCalled, 1);
        strict_1.default.deepEqual(calls.cdpSend[0], ['Network.clearBrowserCache']);
        strict_1.default.equal(calls.detachCalled, 1);
        strict_1.default.equal(calls.reload.length, 1);
        strict_1.default.deepEqual(calls.reload[0][0], { waitUntil: 'networkidle', timeout: 60_000 });
        strict_1.default.equal(result.success, true);
        strict_1.default.equal(result.errorMessage, '');
        strict_1.default.ok(result.reloadMs >= 0);
    });
    (0, node_test_1.it)('возвращает success=false и error при падении reload', async () => {
        const { page } = mockPage({
            reload: () => Promise.reject(new Error('navigation failed')),
        });
        const result = await (0, hard_reload_js_1.hardReloadPage)(page, true);
        strict_1.default.equal(result.success, false);
        strict_1.default.match(result.errorMessage, /navigation failed/);
    });
    (0, node_test_1.it)('пропускает clearBrowserCache, если bypassCache=false', async () => {
        const { page, calls } = mockPage();
        await (0, hard_reload_js_1.hardReloadPage)(page, false);
        strict_1.default.equal(calls.newCDPSessionCalled, 0);
        strict_1.default.equal(calls.reload.length, 1);
    });
});
//# sourceMappingURL=hard-reload.test.js.map