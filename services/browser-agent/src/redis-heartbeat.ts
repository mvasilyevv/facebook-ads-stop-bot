/**
 * Периодическая запись heartbeat-ключа в Redis.
 *
 * Читатель ключа: apps/api/routers/v1/settings_vision.py::_read_runtime_from_redis
 * Ожидаемые поля JSON: status, cdp_ready, cdp_port, message, ts
 *
 * Redis-клиент держится отдельно от gRPC-логики, чтобы сбой Redis
 * не влиял на работу browser-agent.
 */

import type { SessionManager } from './session-manager.js';

// Интервал записи heartbeat в миллисекундах (TTL ключа 60с — с запасом 2×).
const HEARTBEAT_INTERVAL_MS = 20_000;
// TTL ключа в Redis (секунды).
const HEARTBEAT_TTL_SEC = 60;
// Имя ключа — совпадает с _BROWSER_AGENT_HEARTBEAT_KEY в settings_vision.py.
const HEARTBEAT_KEY = 'worker:heartbeat:browser-agent';

/** Минимальный интерфейс Redis-клиента (ioredis или совместимый mock). */
export interface RedisLike {
  set(
    key: string,
    value: string,
    expiryMode: 'EX',
    time: number,
  ): Promise<unknown>;
  quit(): Promise<unknown>;
}

/** Фабрика Redis-клиента — ленивый импорт ioredis (чтобы не падать на старте если нет пакета). */
async function createRedisClient(url: string): Promise<RedisLike> {
  // ioredis добавлен в dependencies при установке задачи.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { default: Redis } = await import('ioredis');
  return new Redis(url, {
    // Не ломать browser-agent при падении Redis — тихая переподключение.
    enableOfflineQueue: false,
    lazyConnect: true,
    maxRetriesPerRequest: 0,
    // Логировать ошибки Redis в stderr, не выбрасывать.
    enableReadyCheck: false,
  }) as unknown as RedisLike;
}

/** Возвращает payload heartbeat по текущему состоянию сессий. */
export function buildHeartbeatPayload(sessionManager: SessionManager): string {
  // Ищем активную сессию с живым CDP.
  let cdpReady = false;
  let cdpPort: number | null = null;
  let message = 'gRPC-сервер запущен';

  try {
    // getPreferredSession() бросает если активных сессий нет — ловим ниже.
    const session = sessionManager.getPreferredSession();
    cdpReady = session.status === 'connected' && session.browser !== null;
    cdpPort = cdpReady ? (session.cdpPort ?? null) : null;
    message = cdpReady
      ? `CDP подключён (порт ${cdpPort})`
      : 'Сессия найдена, но CDP не готов';
  } catch {
    // Активной сессии нет — сервис работает, CDP не подключён.
    message = 'Активная CDP-сессия отсутствует';
  }

  const payload = {
    status: 'ONLINE',
    cdp_ready: cdpReady,
    cdp_port: cdpPort,
    message,
    ts: Math.floor(Date.now() / 1000),
  };
  return JSON.stringify(payload);
}

/**
 * Запускает фоновый таймер heartbeat.
 *
 * @param sessionManager — источник состояния CDP (передаётся из index.ts).
 * @returns функция остановки (вызвать при graceful shutdown).
 */
export async function startHeartbeat(
  sessionManager: SessionManager,
): Promise<() => Promise<void>> {
  // Redis URL: из env или дефолт для хост-режима browser-agent.
  // В Docker-сети Python-воркеры используют redis://redis:6379/0,
  // но browser-agent работает на хосте, поэтому дефолт — 127.0.0.1:6380.
  const redisUrl =
    process.env.REDIS_URL ?? 'redis://127.0.0.1:6380/0';

  let redis: RedisLike | null = null;
  try {
    redis = await createRedisClient(redisUrl);
  } catch (err) {
    console.error('[heartbeat] Не удалось создать Redis-клиент, heartbeat отключён:', err);
    // Возвращаем no-op stop, чтобы не ронять сервис.
    return async () => {};
  }

  /** Разовая запись в Redis. */
  async function writeHeartbeat(): Promise<void> {
    if (!redis) return;
    try {
      const payload = buildHeartbeatPayload(sessionManager);
      await redis.set(HEARTBEAT_KEY, payload, 'EX', HEARTBEAT_TTL_SEC);
    } catch (err) {
      // Ошибка Redis не должна ронять browser-agent — только лог.
      console.error('[heartbeat] Ошибка записи Redis heartbeat:', err);
    }
  }

  // Первая запись сразу при старте.
  await writeHeartbeat();

  const timer = setInterval(() => {
    writeHeartbeat().catch((err) => {
      console.error('[heartbeat] Неожиданная ошибка в heartbeat-таймере:', err);
    });
  }, HEARTBEAT_INTERVAL_MS);

  // setInterval не должен держать event loop — браузер-агент управляется gRPC-сервером.
  timer.unref?.();

  /** Функция graceful shutdown. */
  async function stop(): Promise<void> {
    clearInterval(timer);
    if (redis) {
      try {
        await redis.quit();
      } catch {
        // best-effort при завершении
      }
      redis = null;
    }
  }

  return stop;
}
