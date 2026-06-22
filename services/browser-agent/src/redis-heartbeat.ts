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
  connect(): Promise<unknown>;
  quit(): Promise<unknown>;
  // status — текущее состояние соединения ioredis (ready|connecting|reconnecting|close|end|…).
  // Пишем heartbeat только при 'ready', иначе команда падает "Stream isn't writeable".
  readonly status: string;
  on(event: string, listener: (...args: unknown[]) => void): unknown;
}

/** Фабрика Redis-клиента — ленивый импорт ioredis (чтобы не падать на старте если нет пакета). */
async function createRedisClient(url: string): Promise<RedisLike> {
  // ioredis добавлен в dependencies при установке задачи.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { default: Redis } = await import('ioredis');
  const client = new Redis(url, {
    // enableOfflineQueue:false — команда на неготовом сокете падает сразу, но мы и
    // не пишем, пока status !== 'ready' (см. writeHeartbeat), так что спама нет.
    enableOfflineQueue: false,
    lazyConnect: true,
    maxRetriesPerRequest: 1,
    enableReadyCheck: true,
    // КЛЮЧЕВОЕ для живучести: явный retryStrategy = бесконечный реконнект с backoff.
    // Без него ioredis после пересоздания контейнера redis (деплой/ребут/OOM) уходил
    // в нерабочее состояние навсегда — heartbeat молчал часами (resilience-аудит rank 1).
    retryStrategy: (times: number) => Math.min(times * 200, 5000),
    reconnectOnError: () => true,
  });
  // Наблюдаемость без спама и без падения процесса: один лог на серию ошибок,
  // отдельный лог при восстановлении соединения.
  let errLogged = false;
  client.on('error', (err: unknown) => {
    if (!errLogged) {
      console.error('[heartbeat] Redis error (повтор подавлён до reconnect):', err);
      errLogged = true;
    }
  });
  client.on('ready', () => {
    console.error('[heartbeat] Redis подключён/восстановлен');
    errLogged = false;
  });
  return client as unknown as RedisLike;
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
    // Пишем только при готовом соединении. При обрыве/реконнекте retryStrategy сам
    // поднимет сокет, а следующая запись по таймеру пройдёт — без спама "Stream isn't
    // writeable" и без вечно-мёртвого heartbeat после пересоздания redis (rank 1).
    if (redis.status !== 'ready') return;
    try {
      const payload = buildHeartbeatPayload(sessionManager);
      await redis.set(HEARTBEAT_KEY, payload, 'EX', HEARTBEAT_TTL_SEC);
    } catch (err) {
      // Ошибка Redis не должна ронять browser-agent — только лог.
      console.error('[heartbeat] Ошибка записи Redis heartbeat:', err);
    }
  }

  // lazyConnect: соединение поднимается явно. Дожидаемся готовности до первой
  // записи, иначе она падает "Stream isn't writeable" (enableOfflineQueue=false)
  // и засоряет логи ложной ошибкой при каждом старте. Сбой connect не критичен —
  // таймер ниже будет переподключаться.
  try {
    await redis.connect();
  } catch (err) {
    console.error('[heartbeat] Первое подключение к Redis не удалось (повтор по таймеру):', err);
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
