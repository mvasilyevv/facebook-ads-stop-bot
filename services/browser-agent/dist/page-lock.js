"use strict";
// Per-session async-мьютекс над общей primaryPage (H-7 / аудит BA-4).
//
// Проблема: скан (am-fetch.acquireGraphContext → page.reload) и Marketing API
// мутация (meta-api.executeGraphCall → page.evaluate(fetch)) работают над ОДНОЙ
// session.primaryPage. reload во время in-flight fetch мутации рвёт execution
// context («Execution context was destroyed») → спорадический провал auto-stop
// именно под нагрузкой (много алертов → много мутаций + частые сканы).
//
// Решение: сериализуем операции над страницей по session_id. Обе стороны (скан и
// мутация) берут один и тот же лок → reload и evaluate(fetch) не пересекаются.
//
// Реализация — цепочка промисов: каждый вызов ждёт завершения предыдущего держателя
// (успех ИЛИ ошибка — .catch гасит, чтобы один сбой не порвал очередь), потом fn.
Object.defineProperty(exports, "__esModule", { value: true });
exports.withPageLock = withPageLock;
exports._resetPageLocks = _resetPageLocks;
const _tails = new Map();
const _DEFAULT_KEY = '__default__';
function withPageLock(sessionId, fn) {
    const key = sessionId && sessionId.trim() ? sessionId.trim() : _DEFAULT_KEY;
    const prev = _tails.get(key) ?? Promise.resolve();
    // Следующий стартует ПОСЛЕ предыдущего (ошибку предыдущего игнорируем для очереди).
    const run = prev.catch(() => { }).then(() => fn());
    // Хвост очереди не должен «отравляться» rejection'ом — гасим в хвосте отдельно.
    _tails.set(key, run.catch(() => { }));
    return run;
}
// Только для тестов: сбросить состояние очередей.
function _resetPageLocks() {
    _tails.clear();
}
//# sourceMappingURL=page-lock.js.map