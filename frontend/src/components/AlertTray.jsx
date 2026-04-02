import { ALERT_STATE_LABELS } from '../constants/alertStates.js';

// Live-поток активных инцидентов для Dashboard
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
    if (incident.latest_disable_task_status === 'FAILED') {
      return { label: 'Ошибка', color: 'var(--accent-crimson)', bg: 'var(--accent-crimson-dim)' };
    }
    if (incident.needs_manual_attention) {
      return { label: 'Требует внимания', color: 'var(--accent-gold)', bg: 'var(--accent-gold-dim)' };
    }
    if (incident.has_active_disable_task) {
      return { label: 'В очереди', color: 'var(--text-muted)', bg: 'var(--bg-tertiary)' };
    }
    if (incident.current_state === 'CLAIMED') {
      return { label: 'Завершается', color: 'var(--text-muted)', bg: 'var(--bg-tertiary)' };
    }
    return null;
  }

  // Сортировка: STOP → WARNING → EARLY_SIGNAL → CLAIMED
  const stateOrder = { STOP_SENT: 0, WARNING_SENT: 1, EARLY_SIGNAL_SENT: 2, CLAIMED: 3 };
  const sorted = [...incidents].sort(
    (a, b) => (stateOrder[a.current_state] ?? 99) - (stateOrder[b.current_state] ?? 99)
  );

  const stateConfig = {
    STOP_SENT:         { variant: 'stop' },
    WARNING_SENT:      { variant: 'warning' },
    EARLY_SIGNAL_SENT: { variant: 'signal' },
    CLAIMED:           { variant: 'claimed' },
  };

  const getDisableTask = (fbAdId) =>
    disableTasks.find((t) => t.fb_ad_id === fbAdId);

  const isEmpty = sorted.length === 0;

  return (
    <div className="alert-tray">
      <div className="alert-tray__header">
        <span className={`alert-tray__live-dot${!isEmpty ? ' alert-tray__live-dot--error' : ''}`} />
        <span className="alert-tray__title">Активные сигналы</span>
        {!isEmpty && <span className="alert-tray__count">{sorted.length}</span>}
      </div>

      <div className="alert-tray__body">
        {isEmpty && settings !== null && settings.is_scanning_enabled && (
          <div className="alert-tray__empty">
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>Всё чисто</div>
            {lastScanAt && (
              <div style={{ fontSize: '11px', fontFamily: "'JetBrains Mono', monospace" }}>
                {new Date(lastScanAt).toLocaleTimeString('ru-RU')}
              </div>
            )}
          </div>
        )}

        {isEmpty && settings !== null && !settings.is_scanning_enabled && (
          <div className="alert-tray__empty">
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '10px' }}>Сканирование приостановлено</div>
            {onEnableScanning && (
              <button className="btn btn-primary btn-sm" onClick={onEnableScanning}>
                Включить сканирование
              </button>
            )}
          </div>
        )}

        {isEmpty && settings === null && (
          <div style={{ padding: '40px 20px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  style={{
                    height: '16px',
                    background: 'linear-gradient(90deg, var(--bg-raised) 0%, var(--bg-tertiary) 50%, var(--bg-raised) 100%)',
                    backgroundSize: '200% 100%',
                    borderRadius: '3px',
                    animation: 'pulse 2s infinite',
                  }}
                />
              ))}
            </div>
          </div>
        )}

        {sorted.map((incident) => {
          const cfg = stateConfig[incident.current_state];
          const task = getDisableTask(incident.fb_ad_id);
          const reason = incident.reason_title || (incident.matched_rule_codes ? incident.matched_rule_codes.join(', ') : incident.current_state);
          const badge = getProcessingBadge(incident);

          return (
            <div
              key={incident.fb_ad_id}
              className={`alert-tray__item alert-tray__item--${cfg?.variant || 'default'}`}
              onClick={() => onSelectIncident(incident)}
              style={{ cursor: 'pointer' }}
            >
              <span className={`state-dot state-dot--${cfg?.variant || 'default'}`} />

              <div className="alert-tray__item-body">
                <div className="alert-tray__item-name">{incident.ad_name}</div>
                <div className="alert-tray__item-reason">{reason}</div>
              </div>

              {/* Время создания + последняя активность */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px', flexShrink: 0 }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 600 }}>
                  {absTime(incident.started_at || incident.last_activity_at)}
                </div>
                {incident.started_at && incident.last_activity_at !== incident.started_at && (
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '10px', color: 'var(--text-muted)' }}>
                    {relTime(incident.last_activity_at)}
                  </div>
                )}
                {badge && (
                  <div style={{
                    fontSize: '10px', fontWeight: 600,
                    color: badge.color, background: badge.bg,
                    padding: '1px 5px', borderRadius: '3px',
                    marginTop: '2px',
                  }}>
                    {badge.label}
                  </div>
                )}
              </div>

              <div className="alert-tray__item-action" onClick={(e) => e.stopPropagation()}>
                {incident.current_state === 'STOP_SENT' && task && (
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Отключаем...</span>
                )}
                {incident.current_state === 'STOP_SENT' && !task && (
                  <button
                    className="btn-disable-inline"
                    onClick={() => onDisable(incident.fb_ad_id, incident.ad_name)}
                  >
                    Отключить
                  </button>
                )}
                {incident.current_state === 'WARNING_SENT' && (
                  <button
                    className="btn-disable-inline btn-disable-inline--warning"
                    onClick={() => onDisable(incident.fb_ad_id, incident.ad_name)}
                  >
                    Вручную
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
