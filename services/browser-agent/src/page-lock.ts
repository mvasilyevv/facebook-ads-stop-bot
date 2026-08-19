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

export interface PageLockOptions {
  /** Дедлайн операции. По его срабатыванию ожидание очереди бросается. */
  signal?: AbortSignal;
}

export function withPageRoleLock<T>(
  sessionId: string | undefined,
  role: PageLockRole,
  cabinetId: string | undefined,
  fn: () => Promise<T>,
  options: PageLockOptions = {},
): Promise<T> {
  return withPageLock(pageLockKey(sessionId, role, cabinetId), fn, options);
}

/** Ожидание своей очереди, прерываемое дедлайном операции. */
function awaitTurn(previous: Promise<unknown>, signal?: AbortSignal): Promise<void> {
  const queued = previous.then(
    () => undefined,
    () => undefined,
  );
  if (!signal) return queued;
  if (signal.aborted) {
    return Promise.reject(new Error('page lock wait aborted before it started'));
  }
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => reject(new Error('page lock wait aborted by operation deadline'));
    signal.addEventListener('abort', onAbort, { once: true });
    void queued.then(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    });
  });
}

export function withPageLock<T>(
  sessionId: string | undefined,
  fn: () => Promise<T>,
  options: PageLockOptions = {},
): Promise<T> {
  const key = sessionId && sessionId.trim() ? sessionId.trim() : _DEFAULT_KEY;
  const prev = _tails.get(key) ?? Promise.resolve();
  // Слот держателя отделён от ожидания. Хвост очереди указывает на слот, а не на
  // сам вызов: отказавшийся ждать освобождает слот немедленно и не пускает
  // следующего вперёд живого держателя — взаимное исключение важнее скорости.
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  _tails.set(
    key,
    prev.then(
      () => held,
      () => held,
    ),
  );
  const run = (async () => {
    try {
      await awaitTurn(prev, options.signal);
    } catch (error) {
      // Очередь не наша: слот отдаём сразу, иначе следующий ждал бы отменённого.
      release();
      throw error;
    }
    try {
      return await fn();
    } finally {
      release();
    }
  })();
  return run;
}

// Только для тестов: сбросить состояние очередей.
export function _resetPageLocks(): void {
  _tails.clear();
}
