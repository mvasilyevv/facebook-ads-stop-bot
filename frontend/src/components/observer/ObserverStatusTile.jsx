import { useEffect, useMemo, useState } from 'react';

import { getObserverStatus, startNewCabinetDay } from '../../api.js';
import ScanRunsHistoryModal from './ScanRunsHistoryModal.jsx';

// Маппинг outcome последнего цикла / worker_status → бейдж.
const OUTCOME_BADGES = {
  OK: {
    label: 'Сканирую',
    tone: 'bg-success-muted text-success border-success/30',
    dot: true,
  },
  OK_PARTIAL: {
    label: 'Сканирую (неполные данные)',
    tone: 'bg-success-muted text-success border-success/30',
    dot: true,
  },
  EMPTY_OK: { label: 'Кабинет пуст', tone: 'bg-elevated text-muted border-border' },
  EMPTY_BAD: {
    label: 'Не вижу таблицу',
    tone: 'bg-warning/10 text-warning border-warning/30',
  },
  STALE_DATA: {
    label: 'Данные не пришли — перезагружаю',
    tone: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  },
  BROWSER_LOST: {
    label: 'Браузер отвалился — переподключаюсь',
    tone: 'bg-danger-muted text-danger border-danger/30',
  },
  WAITING_BROWSER: {
    label: 'Браузер занят',
    tone: 'bg-warning/10 text-warning border-warning/30',
  },
  PAUSED: { label: 'Выключено', tone: 'bg-elevated text-muted border-border' },
  ERROR: { label: 'Ошибка', tone: 'bg-danger-muted text-danger border-danger/30' },
  RUNNING: {
    label: 'Сканирую',
    tone: 'bg-success-muted text-success border-success/30',
    dot: true,
  },
  IDLE: { label: 'Ожидание', tone: 'bg-elevated text-muted border-border' },
};

const PHASE_LABELS = {
  refresh: 'обновление таблицы',
  scrolling: 'сканирование строк',
  parsing: 'парсинг данных',
  evaluating: 'оценка правил',
  sleeping: 'ожидание следующего цикла',
};

function formatRelative(value) {
  if (!value) return '—';
  const ms = Date.now() - new Date(value).getTime();
  if (ms < 0) return 'через мгновение';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  return `${Math.floor(min / 60)} ч назад`;
}

function formatTimestamp(value) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleTimeString('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

export default function ObserverStatusTile() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
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
    const id = setInterval(fetchOnce, 2000);
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

  const badge = useMemo(() => {
    if (!data) return null;
    // Если сканирование отключено пользователем — показываем PAUSED, остальное игнорируем.
    if (data.is_scanning_enabled === false) return OUTCOME_BADGES.PAUSED;
    const outcome = data.last_run?.outcome;
    const workerStatus = String(data.worker_status || '').toUpperCase();
    const key = outcome || workerStatus || 'IDLE';
    return (
      OUTCOME_BADGES[key] || { label: key, tone: 'bg-elevated text-muted border-border' }
    );
  }, [data]);

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

  const phaseLabel = PHASE_LABELS[data.active_phase] || data.active_phase || '—';
  const lastRun = data.last_run;

  return (
    <>
      <div className="rounded-lg border border-border bg-elevated p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="text-2xs uppercase tracking-wide text-muted">Observer</div>
            <div
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${badge.tone}`}
            >
              {badge.dot && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
              )}
              {badge.label}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              className="rounded border border-border bg-surface px-3 py-1 text-xs text-muted hover:text-text"
            >
              Подробнее
            </button>
            <button
              type="button"
              onClick={handleRollover}
              disabled={rolloverBusy}
              className="rounded border border-border bg-surface px-3 py-1 text-xs text-muted hover:text-text disabled:opacity-50"
            >
              {rolloverBusy ? 'Архивируем…' : 'Сутки'}
            </button>
          </div>
        </div>

        <div className="mb-3 flex items-center justify-between text-xs">
          <div>
            <span className="text-muted">Фаза: </span>
            <span className="text-text">{phaseLabel}</span>
          </div>
          <div>
            <span className="text-muted">Цикл </span>
            <span className="font-mono text-text">#{data.current_scan_id ?? 0}</span>
          </div>
        </div>

        {lastRun && (
          <div className="mb-3 grid grid-cols-4 gap-3 rounded border border-border bg-surface px-3 py-2">
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Объявлений</div>
              <div className="font-mono text-xs">
                {lastRun.rows_total ?? 0} / {data.active_total ?? 0}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">С данными</div>
              <div className="font-mono text-xs">
                {lastRun.rows_with_data ?? 0}
                {lastRun.rows_partial > 0 && (
                  <span className="text-warning"> ({lastRun.rows_partial} неполн.)</span>
                )}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Время</div>
              <div className="font-mono text-xs">
                {lastRun.duration_seconds
                  ? `${lastRun.duration_seconds.toFixed(1)}с`
                  : '—'}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-muted">Угроза</div>
              <div className="font-mono text-xs">{lastRun.threat_level || '—'}</div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-between text-2xs text-muted">
          <div>
            Следующий цикл:{' '}
            {data.next_scan_at ? formatTimestamp(data.next_scan_at) : '—'}
          </div>
          <div>Пульс: {formatRelative(data.worker_heartbeat_at)}</div>
          <div>
            Сутки:{' '}
            {data.cabinet_day_started_at
              ? formatTimestamp(data.cabinet_day_started_at)
              : '—'}
          </div>
        </div>

        {lastRun?.error_message && (
          <div className="mt-2 border-l-2 border-danger/40 pl-2 text-xs text-danger">
            {lastRun.error_message}
          </div>
        )}
      </div>

      {historyOpen && <ScanRunsHistoryModal onClose={() => setHistoryOpen(false)} />}
    </>
  );
}
