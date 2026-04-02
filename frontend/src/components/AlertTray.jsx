// Live-поток активных инцидентов для Dashboard
export function AlertTray({ incidents = [], disableTasks = [], onSelectIncident, onDisable }) {
  function relTime(isoStr) {
    if (!isoStr) return '';
    const diff = (Date.now() - new Date(isoStr)) / 1000;
    if (diff < 60) return `${Math.round(diff)}с`;
    if (diff < 3600) return `${Math.round(diff / 60)}м`;
    return `${Math.round(diff / 3600)}ч`;
  }

  // Сортировка: STOP → WARNING → EARLY_SIGNAL → CLAIMED
  const stateOrder = { STOP_SENT: 0, WARNING_SENT: 1, EARLY_SIGNAL_SENT: 2, CLAIMED: 3 };
  const sorted = [...incidents].sort(
    (a, b) => (stateOrder[a.current_state] ?? 99) - (stateOrder[b.current_state] ?? 99)
  );

  const stateConfig = {
    STOP_SENT: { icon: '🛑', variant: 'stop' },
    WARNING_SENT: { icon: '⚠️', variant: 'warning' },
    EARLY_SIGNAL_SENT: { icon: '🔎', variant: 'signal' },
    CLAIMED: { icon: '🔄', variant: 'claimed' },
  };

  const getDisableTask = (fbAdId) =>
    disableTasks.find((t) => t.fb_ad_id === fbAdId);

  const isEmpty = sorted.length === 0;

  return (
    <div className="alert-tray">
      <div className="alert-tray__header">
        <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--accent-crimson)', animation: 'pulse 2s infinite', marginRight: '8px' }} />
        АКТИВНЫЕ СИГНАЛЫ
        {!isEmpty && <span style={{ marginLeft: 'auto', background: 'var(--bg-tertiary)', padding: '2px 6px', borderRadius: '12px', fontSize: '12px' }}>{sorted.length}</span>}
      </div>

      <div className="alert-tray__body">
        {isEmpty && (
          <div className="alert-tray__empty">
            Нет активных сигналов — всё в норме
          </div>
        )}

        {sorted.map((incident) => {
          const cfg = stateConfig[incident.current_state];
          const task = getDisableTask(incident.fb_ad_id);
          const reason = incident.reason_title || (incident.matched_rule_codes ? incident.matched_rule_codes.join(', ') : incident.current_state);

          return (
            <div
              key={incident.fb_ad_id}
              className={`alert-tray__item alert-tray__item--${cfg?.variant || 'default'}`}
              onClick={() => onSelectIncident(incident)}
              style={{ cursor: 'pointer' }}
            >
              <span style={{ fontSize: '20px', lineHeight: 1 }}>{cfg?.icon}</span>

              <div className="alert-tray__item-body">
                <div className="alert-tray__item-name">{incident.ad_name}</div>
                <div className="alert-tray__item-reason">{reason}</div>
              </div>

              <div className="alert-tray__item-time">{relTime(incident.last_activity_at)}</div>

              <div className="alert-tray__item-action" onClick={(e) => e.stopPropagation()}>
                {incident.current_state === 'STOP_SENT' && task && (
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Отключаем...</span>
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
