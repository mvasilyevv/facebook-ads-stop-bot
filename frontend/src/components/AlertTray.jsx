import { ALERT_STATE_LABELS } from '../constants/alertStates.js';

// Цвета variant → Tailwind классы
const VARIANT_BORDER = {
  stop: 'border-l-danger',
  warning: 'border-l-warning',
  signal: 'border-l-early',
  claimed: 'border-l-muted',
  default: 'border-l-transparent',
};

const VARIANT_DOT = {
  stop: 'bg-danger',
  warning: 'bg-warning',
  signal: 'bg-early',
  claimed: 'bg-muted',
  default: 'bg-muted',
};

// Маппинг badge: processing status
const BADGE_CONFIG = {
  FAILED: { label: 'Ошибка', cls: 'badge-danger' },
  needs_manual: { label: 'Требует внимания', cls: 'badge-warning' },
  in_queue: { label: 'В очереди', cls: 'badge-neutral' },
  completing: { label: 'Завершается', cls: 'badge-neutral' },
};

/** Live-поток активных инцидентов для Dashboard */
export function AlertTray({ incidents = [], disableTasks = [], onSelectIncident, onDisable, settings = null, lastScanAt = null, onEnableScanning = null }) {
  function relTime(isoStr) {
    if (!isoStr) return '';
    const diff = (Date.now() - new Date(isoStr)) / 1000;
    if (diff < 60) return `${Math.round(diff)}с назад`;
    if (diff < 3600) return `${Math.round(diff / 60)}м назад`;
    return `${Math.round(diff / 3600)}ч назад`;
  }

  function absTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const hhmm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    if (sameDay) return hhmm;
    return `${d.getDate().toString().padStart(2, '0')}.${(d.getMonth() + 1).toString().padStart(2, '0')} ${hhmm}`;
  }

  function getProcessingBadge(incident) {
    if (incident.latest_disable_task_status === 'FAILED') return BADGE_CONFIG.FAILED;
    if (incident.needs_manual_attention) return BADGE_CONFIG.needs_manual;
    if (incident.has_active_disable_task) return BADGE_CONFIG.in_queue;
    if (incident.current_state === 'CLAIMED') return BADGE_CONFIG.completing;
    return null;
  }

  const stateOrder = { STOP_SENT: 0, WARNING_SENT: 1, CLAIMED: 2 };
  const sorted = [...incidents].sort(
    (a, b) => (stateOrder[a.current_state] ?? 99) - (stateOrder[b.current_state] ?? 99)
  );

  const stateConfig = {
    STOP_SENT: 'stop',
    WARNING_SENT: 'warning',
    CLAIMED: 'claimed',
  };

  const getDisableTask = (fbAdId) => disableTasks.find((t) => t.fb_ad_id === fbAdId);
  const isEmpty = sorted.length === 0;

  return (
    <div>
      {/* Заголовок */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className={`status-dot ${isEmpty ? 'bg-success' : 'bg-danger animate-pulse-dot'}`} />
        <span className="text-2xs font-bold uppercase tracking-widest text-secondary">
          Активные сигналы
        </span>
        {!isEmpty && (
          <span className="ml-1 rounded-full bg-danger-muted px-2 py-0.5 text-2xs font-bold text-danger">
            {sorted.length}
          </span>
        )}
      </div>

      {/* Содержимое */}
      <div className="divide-y divide-border">
        {/* Пусто: сканирование включено */}
        {isEmpty && settings !== null && settings.is_scanning_enabled && (
          <div className="flex flex-col items-center py-8 text-center">
            <span className="text-sm font-semibold text-primary">Всё чисто</span>
            {lastScanAt && (
              <span className="mt-1 font-mono text-2xs text-muted">
                {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
              </span>
            )}
          </div>
        )}

        {/* Пусто: сканирование выключено */}
        {isEmpty && settings !== null && !settings.is_scanning_enabled && (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <span className="text-sm font-semibold text-primary">Сканирование приостановлено</span>
            {onEnableScanning && (
              <button className="btn-primary text-sm" onClick={onEnableScanning}>
                Включить сканирование
              </button>
            )}
          </div>
        )}

        {/* Скелетон загрузки */}
        {isEmpty && settings === null && (
          <div className="space-y-2 px-4 py-6">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-4 animate-pulse rounded-sm bg-elevated" />
            ))}
          </div>
        )}

        {/* Список инцидентов */}
        {sorted.map((incident) => {
          const variant = stateConfig[incident.current_state] || 'default';
          const task = getDisableTask(incident.fb_ad_id);
          const reason = incident.reason_title || (incident.matched_rule_codes ? incident.matched_rule_codes.join(', ') : incident.current_state);
          const badge = getProcessingBadge(incident);

          return (
            <div
              key={incident.fb_ad_id}
              className={`flex items-start gap-3 border-l-2 px-4 py-3 cursor-pointer transition-colors hover:bg-elevated/50 ${VARIANT_BORDER[variant]}`}
              onClick={() => onSelectIncident(incident)}
            >
              <span className={`status-dot mt-1.5 flex-shrink-0 ${VARIANT_DOT[variant]}`} />

              <div className="flex-1 min-w-0">
                <div className="truncate text-sm font-medium text-primary">{incident.ad_name}</div>
                <div className="truncate text-2xs text-secondary">{reason}</div>
              </div>

              {/* Время + badge */}
              <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
                <span className="font-mono text-2xs font-semibold text-secondary">
                  {absTime(incident.started_at || incident.last_activity_at)}
                </span>
                {incident.started_at && incident.last_activity_at !== incident.started_at && (
                  <span className="font-mono text-[10px] text-muted">
                    {relTime(incident.last_activity_at)}
                  </span>
                )}
                {badge && (
                  <span className={`mt-0.5 ${badge.cls}`}>{badge.label}</span>
                )}
              </div>

              {/* Действие */}
              <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                {incident.current_state === 'STOP_SENT' && task && (
                  <span className="text-2xs text-muted">Отключаем…</span>
                )}
                {incident.current_state === 'STOP_SENT' && !task && (
                  <button
                    className="rounded-sm bg-danger-muted px-2 py-1 text-2xs font-semibold text-danger hover:bg-danger/20"
                    onClick={() => onDisable(incident.fb_ad_id, incident.ad_name)}
                  >
                    Отключить
                  </button>
                )}
                {incident.current_state === 'WARNING_SENT' && (
                  <button
                    className="rounded-sm bg-warning-muted px-2 py-1 text-2xs font-semibold text-warning hover:bg-warning/20"
                    onClick={() => onDisable(incident.fb_ad_id, incident.ad_name)}
                  >
                    Отключить
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
