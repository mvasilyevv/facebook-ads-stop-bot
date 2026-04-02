function statusIcon(s) {
  return { PENDING: '⏳', RUNNING: '🔄', RETRYING: '🔄', SUCCEEDED: '✅', FAILED: '❌' }[s] || '?';
}

function statusColor(s) {
  return { PENDING: 'var(--text-muted)', RUNNING: 'var(--accent-teal)', RETRYING: 'var(--accent-gold)', SUCCEEDED: 'var(--accent-teal)', FAILED: 'var(--accent-crimson)' }[s] || 'var(--text-primary)';
}

export function TaskQueuePanel({ disableTasks = [], enableTasks = [], enableRecs = [], onRetryDisable, onCreateEnableTask }) {
  const isEmpty = disableTasks.length === 0 && enableTasks.length === 0 && enableRecs.length === 0;

  return (
    <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '4px' }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
        Очередь
      </h3>

      {isEmpty && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', textAlign: 'center', padding: '20px 0' }}>
          Очередь пуста — всё в норме
        </div>
      )}

      {/* Отключение */}
      {disableTasks.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--accent-crimson)', marginBottom: '8px' }}>
            ОТКЛЮЧЕНИЕ ({disableTasks.length})
          </div>
          {disableTasks.slice(0, 5).map((task) => (
            <div key={task.id} style={{ fontSize: '12px', padding: '8px', marginBottom: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ flex: 1 }}>
                <div>{task.ad_name || 'N/A'}</div>
                <div style={{ color: statusColor(task.status), fontSize: '11px', marginTop: '2px' }}>
                  {statusIcon(task.status)} {task.status}
                  {task.attempt_count > 1 && <span> ×{task.attempt_count}</span>}
                </div>
              </div>
              {(task.status === 'FAILED' || task.status === 'RETRYING') && (
                <button onClick={() => onRetryDisable?.(task.id)} style={{ marginLeft: '8px', padding: '4px 10px', fontSize: '11px', background: 'var(--accent-crimson)', color: 'white', border: 'none', borderRadius: '3px', cursor: 'pointer' }}>
                  Повтор
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Включение */}
      {enableTasks.length > 0 && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--accent-teal)', marginBottom: '8px' }}>
            ВКЛЮЧЕНИЕ ({enableTasks.length})
          </div>
          {enableTasks.slice(0, 5).map((task) => (
            <div key={task.id} style={{ fontSize: '12px', padding: '8px', marginBottom: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', display: 'flex', alignItems: 'center' }}>
              <div style={{ flex: 1 }}>
                <div>{task.ad_name || 'N/A'}</div>
                <div style={{ color: statusColor(task.status), fontSize: '11px', marginTop: '2px' }}>
                  {statusIcon(task.status)} {task.status}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Рекомендации */}
      {enableRecs.length > 0 && (
        <div>
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--accent-orchid)', marginBottom: '8px' }}>
            РЕКОМЕНДАЦИИ ({enableRecs.length})
          </div>
          {enableRecs.slice(0, 5).map((rec) => (
            <div key={rec.id} style={{ fontSize: '12px', padding: '8px', marginBottom: '6px', background: 'var(--bg-tertiary)', borderRadius: '3px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ flex: 1 }}>
                <div>🔎 {rec.ad_name || 'N/A'}</div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '2px' }}>
                  {rec.reason || 'Рекомендация'}
                </div>
              </div>
              <button onClick={() => onCreateEnableTask?.(rec.id)} style={{ marginLeft: '8px', padding: '4px 10px', fontSize: '11px', background: 'var(--accent-teal)', color: 'white', border: 'none', borderRadius: '3px', cursor: 'pointer' }}>
                Включить
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
