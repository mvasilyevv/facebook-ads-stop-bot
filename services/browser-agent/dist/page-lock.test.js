"use strict";
// H-7 (BA-4): per-session мьютекс сериализует операции над общей primaryPage,
// чтобы scan page.reload и mutation page.evaluate(fetch) не пересекались.
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const node_test_1 = require("node:test");
const strict_1 = __importDefault(require("node:assert/strict"));
const page_lock_js_1 = require("./page-lock.js");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(0, node_test_1.describe)('withPageLock (H-7 per-session mutex)', () => {
    (0, node_test_1.it)('сериализует операции на ОДНОЙ сессии — без наложения', async () => {
        (0, page_lock_js_1._resetPageLocks)();
        const events = [];
        const op = (tag, ms) => (0, page_lock_js_1.withPageLock)('s1', async () => {
            events.push(`${tag}:start`);
            await sleep(ms);
            events.push(`${tag}:end`);
        });
        // A дольше B, но B не должна влезть в середину A.
        await Promise.all([op('A', 30), op('B', 5)]);
        strict_1.default.deepEqual(events, ['A:start', 'A:end', 'B:start', 'B:end']);
    });
    (0, node_test_1.it)('РАЗНЫЕ сессии выполняются конкурентно', async () => {
        (0, page_lock_js_1._resetPageLocks)();
        const events = [];
        const op = (sid, tag, ms) => (0, page_lock_js_1.withPageLock)(sid, async () => {
            events.push(`${tag}:start`);
            await sleep(ms);
            events.push(`${tag}:end`);
        });
        await Promise.all([op('s1', 'A', 30), op('s2', 'B', 5)]);
        // Обе стартуют сразу (лок не общий), короткая B заканчивается раньше длинной A.
        strict_1.default.equal(events[0], 'A:start');
        strict_1.default.equal(events[1], 'B:start');
        strict_1.default.equal(events[2], 'B:end');
        strict_1.default.equal(events[3], 'A:end');
    });
    (0, node_test_1.it)('ошибка одной операции НЕ ломает очередь сессии', async () => {
        (0, page_lock_js_1._resetPageLocks)();
        await strict_1.default.rejects(() => (0, page_lock_js_1.withPageLock)('s1', async () => {
            throw new Error('boom');
        }), /boom/);
        // Следующая операция на той же сессии должна нормально выполниться.
        const r = await (0, page_lock_js_1.withPageLock)('s1', async () => 42);
        strict_1.default.equal(r, 42);
    });
    (0, node_test_1.it)('возвращает результат fn вызывающему', async () => {
        (0, page_lock_js_1._resetPageLocks)();
        strict_1.default.equal(await (0, page_lock_js_1.withPageLock)('s1', async () => 'ok'), 'ok');
        // Пустой sessionId → дефолтный ключ, тоже работает.
        strict_1.default.equal(await (0, page_lock_js_1.withPageLock)('', async () => 'def'), 'def');
    });
});
//# sourceMappingURL=page-lock.test.js.map