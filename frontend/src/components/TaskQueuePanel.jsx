import { TASK_STATUS_LABELS } from '../constants/alertStates.js';

function statusSymbol(s) {
  return { PENDING: '○', RUNNING: '●', RETRYING: '↻', SUCCEEDED: '✓', FAILED: '×' }[s] || '—';
}

const STATUS_COLOR = {
  PENDING: 'text-muted',
  RUNNING: 'text-accent',
  RETRYING: 'text-warning',
  SUCCEEDED: 'text-accent',
  FAILED: 'text-danger',
};

function getStatusLabel(status) {
  return TASK_STATUS_LABELS[status] || status;
}

const ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING', 'RETRYING', 'FAILED']);

/** Компактная панель очереди задач — показывает только активные */
export function TaskQueuePanel({ disableTasks = [], enableTasks = [], enableRecs = [], onRetryDisable, onCreateEnableTask }) {
  const activeDisable = disableTasks.filter((t) => ACTIVE_STATUSES.has(t.status));
  const activeEnable = enableTasks.filter((t) => ACTIVE_STATUSES.has(t.status));
  const isEmpty = activeDisable.length === 0 && activeEnable.length === 0 && enableRecs.length === 0;

  return (
    <div className="p-4">
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Очередь
      </h3>

      {isEmpty && (
        <div className="py-6 text-center text-sm text-muted">
          Очередь пуста — всё в норме
        </div>
      )}

      {/* Отключение */}
      {activeDisable.length > 0 && (
        <div className="mb-4">
          <div className="mb-2 text-2xs font-bold uppercase tracking-widest text-danger">
            ОТКЛЮЧЕНИЕ ({activeDisable.length})
          </div>
          <div className="space-y-1.5">
            {activeDisable.slice(0, 5).map((task) => (
              <div key={task.id} className="flex items-center justify-between rounded bg-elevated px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-primary">{task.ad_name || 'N/A'}</div>
                  <div className={`mt-0.5 font-mono text-2xs ${STATUS_COLOR[task.status] || 'text-primary'}`}>
                    {statusSymbol(task.status)} {getStatusLabel(task.status)}
                    {task.attempt_count > 1 && <span> x{task.attempt_count}</span>}
                  </div>
                </div>
                {(task.status === 'FAILED' || task.status === 'RETRYING') && (
                  <button
                    onClick={() => onRetryDisable?.(task.id)}
                    className="ml-2 rounded-sm bg-danger-muted px-2 py-1 text-2xs font-semibold text-danger hover:bg-danger/20"
                  >
                    Повтор
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Включение */}
      {activeEnable.length > 0 && (
        <div className="mb-4">
          <div className="mb-2 text-2xs font-bold uppercase tracking-widest text-accent">
            ВКЛЮЧЕНИЕ ({activeEnable.length})
          </div>
          <div className="space-y-1.5">
            {activeEnable.slice(0, 5).map((task) => (
              <div key={task.id} className="flex items-center rounded bg-elevated px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-primary">{task.ad_name || 'N/A'}</div>
                  <div className={`mt-0.5 font-mono text-2xs ${STATUS_COLOR[task.status] || 'text-primary'}`}>
                    {statusSymbol(task.status)} {getStatusLabel(task.status)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Рекомендации */}
      {enableRecs.length > 0 && (
        <div>
          <div className="mb-2 text-2xs font-bold uppercase tracking-widest text-early">
            РЕКОМЕНДАЦИИ ({enableRecs.length})
          </div>
          <div className="space-y-1.5">
            {enableRecs.slice(0, 5).map((rec) => (
              <div key={rec.id} className="flex items-center justify-between rounded bg-elevated px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-primary">{rec.ad_name || 'N/A'}</div>
                  <div className="mt-0.5 text-2xs text-muted">{rec.reason || 'Рекомендация'}</div>
                </div>
                <button
                  onClick={() => onCreateEnableTask?.(rec.id)}
                  className="ml-2 rounded-sm bg-accent-muted px-2 py-1 text-2xs font-semibold text-accent hover:bg-accent/20"
                >
                  Включить
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
