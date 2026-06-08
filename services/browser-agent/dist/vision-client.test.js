"use strict";
// H-5 (BA-1): VisionClient оборачивает fetch в AbortController с таймаутом —
// зависший (но живой) Vision-процесс не должен вешать StartBrowser/Reconnect/Stop
// навсегда. Проверяем: зависший fetch → reject по таймауту; успешный → данные.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const vision_client_js_1 = require("./vision-client.js");
(0, node_test_1.describe)('VisionClient fetch timeout (H-5)', () => {
    (0, node_test_1.it)('request таймаутит зависший Vision-ответ (abort)', async () => {
        const orig = globalThis.fetch;
        // fetch, который никогда не отвечает, но уважает signal.abort()
        globalThis.fetch = ((_url, init) => new Promise((_resolve, reject) => {
            init.signal.addEventListener('abort', () => reject(new DOMException('The operation was aborted', 'AbortError')));
        }));
        try {
            const client = new vision_client_js_1.VisionClient('x-token', 'http://127.0.0.1:3030', {
                requestTimeoutMs: 40,
            });
            const start = Date.now();
            await strict_1.default.rejects(() => client.listProfiles(), /timeout/);
            // Должно отвалиться около таймаута, а не висеть.
            strict_1.default.ok(Date.now() - start < 2_000, 'request должен отвалиться по таймауту быстро');
        }
        finally {
            globalThis.fetch = orig;
        }
    });
    (0, node_test_1.it)('request возвращает данные при успешном ответе', async () => {
        const orig = globalThis.fetch;
        globalThis.fetch = (async () => new Response(JSON.stringify({ profiles: [{ folder_id: 'f', profile_id: 'p', port: 9222 }] }), { status: 200, headers: { 'content-type': 'application/json' } }));
        try {
            const client = new vision_client_js_1.VisionClient('x-token');
            const profiles = await client.listProfiles();
            strict_1.default.equal(profiles.length, 1);
            strict_1.default.equal(profiles[0].port, 9222);
        }
        finally {
            globalThis.fetch = orig;
        }
    });
    (0, node_test_1.it)('request бросает на не-2xx ответе', async () => {
        const orig = globalThis.fetch;
        globalThis.fetch = (async () => new Response('nope', { status: 500 }));
        try {
            const client = new vision_client_js_1.VisionClient('x-token');
            await strict_1.default.rejects(() => client.listProfiles(), /500/);
        }
        finally {
            globalThis.fetch = orig;
        }
    });
});
//# sourceMappingURL=vision-client.test.js.map