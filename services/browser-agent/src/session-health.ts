// Авто-исцеление Vision-сессии при «живая страница/CDP, но мёртвая сеть».
//
// Болезнь: внутри Vision-браузера периодически отваливается исходящий fetch
// (`TypeError: Failed to fetch` в скане / `code -2 NetworkError` в мутации). Процесс,
// CDP и DOM живы — поэтому существующая лесенка восстановления (NOT_FOUND / «страница
// недоступна») этот случай не ловит, и канал авто-стопа молча мёртв до ручного рестарта.
//
// Здесь — чистый детект и счётчик. Автоматическая реакция ограничена reload
// конкретной role/account страницы; reconnect/restart выполняются только через
// внешний PostgreSQL-backed exclusive maintenance path.

import type { BrowserSession } from './types.js';

// Порог серии сетевых сбоев, после которого запускаем лечение. Внутренний ретрай скана
// (AM_TABULAR_RETRY_DELAYS) уже глотает одиночный блип → сюда долетает устойчивый сбой;
// 2 подряд (≈ два цикла скана / две попытки мутации) = это не случайность.
export const HEAL_NET_FAIL_THRESHOLD = 2;

// Минимальный интервал между попытками лечения — чтобы не дёргать сессию каждый цикл,
// пока предыдущее лечение ещё устаканивается.
export const HEAL_COOLDOWN_MS = 45_000;

// Маркеры «сеть страницы мертва» (fetch упал на транспортном уровне). Это НЕ Graph-ошибка
// в теле ответа (протухший токен / нет прав — лечится re-sniff'ом токена, не рестартом).
const NETWORK_FETCH_MARKERS = [
  'failed to fetch',
  'networkerror',
  'err_network',
  'err_connection',
  'err_internet',
  'net::',
  'fetch failed',
  'load failed',
];

/** Сетевая ли это ошибка fetch (транспорт мёртв), а не Graph-ошибка в теле ответа. */
export function isNetworkFetchError(detail: string | null | undefined): boolean {
  if (!detail) return false;
  const s = String(detail).toLowerCase();
  return NETWORK_FETCH_MARKERS.some((m) => s.includes(m));
}

/** Учесть исход fetch-операции: успех сбрасывает серию, сбой — инкремент. */
export function recordFetchOutcome(session: BrowserSession, ok: boolean): void {
  if (ok) {
    session.netFailureStreak = 0;
    return;
  }
  session.netFailureStreak = (session.netFailureStreak ?? 0) + 1;
}

/** Пора ли лечить: серия достигла порога И прошёл cooldown с прошлой попытки. */
export function shouldHealNow(session: BrowserSession, nowMs: number): boolean {
  if ((session.netFailureStreak ?? 0) < HEAL_NET_FAIL_THRESHOLD) return false;
  const last = session.lastHealAt ? session.lastHealAt.getTime() : 0;
  return nowMs - last >= HEAL_COOLDOWN_MS;
}
