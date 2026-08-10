// Per-page-role async mutex.
//
// Scan and control pages are physically distinct. Operations on one concrete
// page still need serialization, but scan and money operations deliberately use
// different keys so a slow scan/reload cannot queue pause/activate behind it.
//
// Реализация — цепочка промисов: каждый вызов ждёт завершения предыдущего держателя
// (успех ИЛИ ошибка — .catch гасит, чтобы один сбой не порвал очередь), потом fn.

const _tails = new Map<string, Promise<unknown>>();

const _DEFAULT_KEY = '__default__';

export type PageLockRole = 'scan' | 'control' | 'interactive' | 'session';

function normalizePart(value: string | undefined, fallback: string): string {
  const normalized = String(value || '').trim();
  return normalized || fallback;
}

export function pageLockKey(
  sessionId: string | undefined,
  role: PageLockRole,
  cabinetId?: string,
): string {
  return [
    normalizePart(sessionId, _DEFAULT_KEY),
    role,
    normalizePart(String(cabinetId || '').replace(/^act_/, ''), '__default__'),
  ].join(':');
}

export function withPageRoleLock<T>(
  sessionId: string | undefined,
  role: PageLockRole,
  cabinetId: string | undefined,
  fn: () => Promise<T>,
): Promise<T> {
  return withPageLock(pageLockKey(sessionId, role, cabinetId), fn);
}

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
