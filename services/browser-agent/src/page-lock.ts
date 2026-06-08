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

const _tails = new Map<string, Promise<unknown>>();

const _DEFAULT_KEY = '__default__';

export function withPageLock<T>(sessionId: string | undefined, fn: () => Promise<T>): Promise<T> {
  const key = sessionId && sessionId.trim() ? sessionId.trim() : _DEFAULT_KEY;
  const prev = _tails.get(key) ?? Promise.resolve();
  // Следующий стартует ПОСЛЕ предыдущего (ошибку предыдущего игнорируем для очереди).
  const run = prev.catch(() => {}).then(() => fn());
  // Хвост очереди не должен «отравляться» rejection'ом — гасим в хвосте отдельно.
  _tails.set(
    key,
    run.catch(() => {}),
  );
  return run;
}

// Только для тестов: сбросить состояние очередей.
export function _resetPageLocks(): void {
  _tails.clear();
}
