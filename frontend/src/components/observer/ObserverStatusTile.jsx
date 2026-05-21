import { useEffect, useState } from 'react';

import { getObserverStatus, startNewCabinetDay } from '../../api.js';

// Маппинг worker_status / guard-причин на цвет бейджа и человекочитаемый лейбл.
const STATUS_META = {
  RUNNING: { label: 'Работает', tone: 'bg-success-muted text-success border-success/30' },
  SCANNING: { label: 'Сканирует', tone: 'bg-accent-muted text-accent border-accent/30' },
  PAUSED: { label: 'Выключен', tone: 'bg-elevated text-muted border-border' },
  WAITING_BROWSER: {
    label: 'Ждёт браузер',
    tone: 'bg-warning/10 text-warning border-warning/30',
  },
  GUARD_PENDING_ZERO: {
    label: 'Guard: ждёт zero',
    tone: 'bg-warning/10 text-warning border-warning/30',
  },
  GUARD_PENDING_PARTIAL: {
    label: 'Guard: ждёт partial',
    tone: 'bg-warning/10 text-warning border-warning/30',
  },
  ERROR: { label: 'Ошибка', tone: 'bg-danger-muted text-danger border-danger/30' },
};

function formatTimestamp(value) {
  if (!value) return '—';
  try {
    const date = new Date(value);
    return date.toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function formatRelative(value) {
  if (!value) return '—';
  const ms = Date.now() - new Date(value).getTime();
  if (ms < 0) return 'через мгновение';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hours = Math.floor(min / 60);
  return `${hours} ч назад`;
}

export default function ObserverStatusTile() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [rolloverBusy, setRolloverBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    const fetchOnce = async () => {
      try {
        const payload = await getObserverStatus();
        if (alive) {
          setData(payload);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e?.message || 'Не удалось загрузить статус');
      }
    };
    fetchOnce();
    const id = setInterval(fetchOnce, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const handleRollover = async () => {
    const ads = data?.active_total ?? 0;
    if (!window.confirm(`Закрыть текущий день и архивировать ${ads} объявлений?`)) return;
    setRolloverBusy(true);
    try {
      const result = await startNewCabinetDay();
      window.alert(`Новые сутки открыты, архивировано ${result.archived_ads} объявлений.`);
    } catch (e) {
      window.alert(`Ошибка: ${e?.message || e}`);
    } finally {
      setRolloverBusy(false);
    }
  };

  if (error && !data) {
    return (
      <div className="rounded-lg border border-danger/30 bg-danger-muted px-3 py-2 text-xs text-danger">
        Observer: ошибка загрузки статуса ({error})
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-border bg-elevated px-3 py-2 text-xs text-muted">
        Observer: загрузка…
      </div>
    );
  }

  const statusKey = String(data.worker_status || 'RUNNING').toUpperCase();
  const meta = STATUS_META[statusKey] || {
    label: statusKey,
    tone: 'bg-elevated text-muted border-border',
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-elevated px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted">Observer</div>
          <div
            className={`mt-0.5 inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${meta.tone}`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {meta.label}
          </div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted">Цикл</div>
          <div className="font-mono text-xs">#{data.current_scan_id ?? 0}</div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted">Последний батч</div>
          <div className="font-mono text-xs">
            {data.last_batch_size}/{data.active_total}
          </div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted">Последний пульс</div>
          <div className="text-xs">{formatRelative(data.worker_heartbeat_at)}</div>
        </div>
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted">Сутки кабинета</div>
          <div className="text-xs">
            {data.cabinet_day_started_at ? formatTimestamp(data.cabinet_day_started_at) : '—'}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={handleRollover}
        disabled={rolloverBusy}
        className="rounded border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-elevated hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
      >
        {rolloverBusy ? 'Архивируем…' : 'Начать новые сутки'}
      </button>
    </div>
  );
}
