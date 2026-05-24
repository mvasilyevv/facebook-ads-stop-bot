import { useCallback, useEffect, useState } from 'react';
import { useAsyncPolling } from '../hooks/useAsyncPolling.js';
import { useWebSocket } from '../hooks/useWebSocket.js';
import { getHealthDetails } from '../api.js';

// Человекочитаемые имена воркеров
const WORKER_LABELS = {
  observer: 'Observer',
  telegram_poller: 'Telegram',
  disable: 'Disable',
  enable: 'Enable',
  enable_recommendation: 'AutoEnable',
  health_watchdog: 'Watchdog',
};

// Порядок отображения воркеров
const WORKER_ORDER = [
  'observer',
  'telegram_poller',
  'disable',
  'enable',
  'enable_recommendation',
  'health_watchdog',
];

/** Форматирует количество секунд в читаемый вид: «2м 34с» или «45с». */
function formatAge(seconds) {
  if (seconds == null) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}с`;
  return `${Math.floor(s / 60)}м ${s % 60}с`;
}

/** Форматирует ISO-дату в локальное время. */
function formatTime(isoStr) {
  if (!isoStr) return '—';
  try {
    return new Date(isoStr).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '—';
  }
}

/** Возвращает цвет Tailwind-класса и dot-цвет по состоянию здоровья. */
function statusColor(healthy, known) {
  if (!known) return { dot: 'bg-[var(--text-muted)]', chip: 'bg-[var(--surface-2)] text-[var(--text-dim)]' };
  if (healthy) return { dot: 'bg-[var(--ok)]', chip: 'bg-[var(--surface-2)] text-[var(--text)]' };
  return { dot: 'bg-[var(--stop)]', chip: 'bg-[var(--surface-2)] text-[var(--stop)]' };
}

/** Чип отдельного воркера. */
function WorkerChip({ label, healthy, tooltip, pulse }) {
  const colors = statusColor(healthy, healthy != null);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${colors.chip} border border-[var(--border)]`}
      title={tooltip}
      aria-label={tooltip}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full flex-shrink-0 ${colors.dot}${pulse ? ' animate-pulse' : ''}`}
      />
      {label}
    </span>
  );
}

/** Чип внешнего сервиса (Vision, browser-agent). */
function ServiceChip({ label, healthy, error }) {
  const tooltip = healthy
    ? `${label}: работает`
    : `${label}: недоступен${error ? ' — ' + error : ''}`;
  return <WorkerChip label={label} healthy={healthy} tooltip={tooltip} pulse={healthy} />;
}

/** Мини-индикатор очереди задач. */
function QueueBadge({ label, count }) {
  if (!count) return null;
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] bg-[var(--surface-2)] text-[var(--warn)] border border-[var(--border)]"
      title={`${label}: ${count}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--warn)] flex-shrink-0" />
      {count}
    </span>
  );
}

export default function HealthBar() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  // Локальное время для плавного тикинга счётчика «N с назад» без лишних запросов к API
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Функция перезапроса health-данных (переиспользуется polling и WS)
  const fetchHealth = useCallback(async (signal) => {
    try {
      const result = await getHealthDetails(signal);
      setData(result);
      setError(false);
    } catch (e) {
      if (e?.name === 'AbortError') return;
      setError(true);
    }
  }, []);

  // WS-подписка: при событии health:updated перезапрашиваем данные
  const wsUrl = (() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws/dashboard`;
  })();

  const { connected: wsConnected } = useWebSocket(wsUrl, {
    enabled: true,
    autoReconnect: true,
    onMessage: (event) => {
      if (event.type === 'health_updated') {
        // При событии — немедленно рефетч без ожидания polling
        void fetchHealth();
      }
    },
  });

  // Polling: если WS подключён — реже (30с), иначе каждые 10с (fallback)
  useAsyncPolling(
    fetchHealth,
    { enabled: true, intervalMs: wsConnected ? 30_000 : 10_000, runImmediately: true },
  );

  // Если ошибка и ещё нет данных — серая заглушка
  if (error && !data) {
    return (
      <div
        className="flex h-7 items-center gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3 font-mono text-[10px] text-[var(--text-muted)]"
        role="status"
        aria-label="Статус системы недоступен"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-muted)]" />
        <span>health: недоступно</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div
        className="flex h-7 items-center gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3 font-mono text-[10px] text-[var(--text-muted)]"
        role="status"
        aria-label="Загрузка статуса системы"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-muted)] animate-pulse" />
        <span>загрузка…</span>
      </div>
    );
  }

  const workers = data.workers ?? {};
  const queues = data.queues ?? {};
  const lastScan = data.last_successful_scan ?? {};
  const disablePending = (queues.disable_pending ?? 0) + (queues.disable_running ?? 0);
  const enablePending = (queues.enable_pending ?? 0) + (queues.enable_running ?? 0);

  return (
    <div
      className="flex h-7 items-center gap-1.5 border-b border-[var(--border)] bg-[var(--surface)] px-3 overflow-x-auto"
      role="status"
      aria-label="Статус системы"
    >
      {/* Воркеры */}
      {WORKER_ORDER.map((key) => {
        const w = workers[key];
        if (!w) return null;
        const label = WORKER_LABELS[key] ?? key;
        const tooltip = `Воркер ${label}: ${w.healthy ? 'работает' : 'не отвечает'}${
          w.heartbeat_age_seconds != null ? ` · ${formatAge(w.heartbeat_age_seconds)} назад` : ''
        }${w.last_heartbeat_at ? ` · ${formatTime(w.last_heartbeat_at)}` : ''}`;
        return (
          <WorkerChip
            key={key}
            label={label}
            healthy={w.healthy}
            tooltip={tooltip}
            pulse={w.healthy}
          />
        );
      })}

      {/* Внешние сервисы */}
      <ServiceChip
        label="Vision"
        healthy={data.vision?.healthy ?? false}
        error={data.vision?.error}
      />
      <ServiceChip
        label="Browser"
        healthy={data.browser_agent?.healthy ?? false}
        error={data.browser_agent?.error}
      />

      {/* Очереди */}
      <QueueBadge label="Очередь отключений" count={disablePending} />
      <QueueBadge label="Очередь включений" count={enablePending} />

      {/* Пульс сканирования: вычисляем возраст локально по тикеру, а не из серверного age_seconds.
          Это гарантирует монотонный рост «N с назад» между refetch'ами и устраняет скачки. */}
      <span className="ml-auto flex-shrink-0 font-mono text-[10px] text-[var(--text-muted)] whitespace-nowrap">
        {lastScan.at
          ? `скан: ${formatAge(Math.floor((now - new Date(lastScan.at).getTime()) / 1000))} назад`
          : 'скан: —'}
      </span>
    </div>
  );
}
