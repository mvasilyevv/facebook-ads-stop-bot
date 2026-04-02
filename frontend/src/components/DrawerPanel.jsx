import { useEffect, useState } from 'react';
import ReactDOM from 'react-dom';
import { StateIcon } from './StateIcon';

// Форматирование значений
const fmt$ = (v) => v != null ? `$${Number(v).toFixed(2)}` : '—';
const fmtN = (v) => v != null ? String(v) : '—';
const fmtPct = (v) => v != null ? `${Number(v).toFixed(2)}%` : '—';

// Статус иконки и цвета
function statusIcon(s) {
  return { PENDING: '⏳', RUNNING: '🔄', RETRYING: '🔄', SUCCEEDED: '✅', FAILED: '❌' }[s] || '?';
}

function statusColor(s) {
  return { PENDING: 'var(--text-muted)', RUNNING: 'var(--accent-teal)', RETRYING: 'var(--accent-gold)', SUCCEEDED: 'var(--accent-teal)', FAILED: 'var(--accent-crimson)' }[s] || 'var(--text-primary)';
}

function RetryCountdown({ nextRetryAt }) {
  const [secs, setSecs] = useState(() => Math.max(0, Math.ceil((new Date(nextRetryAt) - Date.now()) / 1000)));
  useEffect(() => {
    if (secs <= 0) return;
    const t = setInterval(() => setSecs(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [nextRetryAt, secs]);
  return <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Повтор через {secs}с</span>;
}

function DrawerContent({ ad, incident, disableTask, onClose, onDisable, onRetry }) {
  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose]);

  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  if (!ad) return null;

  const metrics = [
    { label: 'Spend', value: fmt$(ad.metrics?.spend) },
    { label: 'CPC', value: fmt$(ad.metrics?.cpc) },
    { label: 'CPL', value: fmt$(ad.metrics?.cpl) },
    { label: 'CPR', value: fmt$(ad.metrics?.cpr) },
    { label: 'Leads', value: fmtN(ad.metrics?.leads) },
    { label: 'Regs', value: fmtN(ad.metrics?.regs) },
    { label: 'Deposits', value: fmtN(ad.metrics?.deposits) },
    { label: 'CTR', value: fmtPct(ad.metrics?.ctr) },
    { label: 'LPV', value: fmtN(ad.metrics?.lpv) },
  ].filter((m) => m.value !== '—');

  const getRuleSeverity = (ruleCode) => {
    if (!incident || !incident.rule_hits) return 'default';
    const hit = incident.rule_hits.find((h) => h.rule_code === ruleCode);
    return hit?.stage === 'STOP' ? 'stop' : 'warning';
  };

  const canDisable = ['WARNING_SENT', 'STOP_SENT'].includes(ad.state);
  const showRetry = disableTask && disableTask.status === 'FAILED';

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer-panel">
        {/* Header */}
        <div className="drawer-panel__header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
            <StateIcon state={ad.state} size="lg" />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>{ad.name}</div>
              {ad.campaign_name && (
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  {ad.campaign_name} {ad.adset_name ? `› ${ad.adset_name}` : ''}
                </div>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              fontSize: '20px',
              cursor: 'pointer',
              padding: '4px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="drawer-panel__body">
          {/* Metrics Grid */}
          {metrics.length > 0 && (
            <div className="drawer-metrics">
              {metrics.map((m) => (
                <div key={m.label} style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>
                    {m.label}
                  </div>
                  <div style={{ fontWeight: 600, fontSize: '13px' }}>
                    {m.value}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Rules Section */}
          {incident && incident.rule_hits && incident.rule_hits.length > 0 && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '8px' }}>
                Сработаны правила
              </div>
              {incident.rule_hits.map((hit) => {
                const severity = hit.stage === 'STOP' ? 'stop' : 'warning';
                const color = severity === 'stop' ? 'var(--accent-crimson)' : 'var(--accent-gold)';
                return (
                  <div
                    key={hit.rule_code}
                    style={{
                      fontSize: '12px',
                      padding: '6px 8px',
                      marginBottom: '4px',
                      borderLeft: `3px solid ${color}`,
                      backgroundColor: 'rgba(0, 0, 0, 0.02)',
                    }}
                  >
                    {hit.rule_code}: {hit.message}
                  </div>
                );
              })}
            </div>
          )}

          {/* Disable Task Status */}
          {disableTask && (
            <div style={{ marginTop: '12px', padding: '10px', background: 'var(--bg-tertiary)', borderRadius: '4px', fontSize: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                <span>Задача на отключение</span>
                <span>Попытка {disableTask.attempt_count || 1}</span>
              </div>
              <div style={{ color: statusColor(disableTask.status) }}>
                {statusIcon(disableTask.status)} {disableTask.status}
              </div>
              {disableTask.last_error && (
                <div style={{ color: 'var(--accent-crimson)', marginTop: '4px', fontSize: '11px' }}>
                  {String(disableTask.last_error).slice(0, 80)}
                </div>
              )}
              {disableTask.next_retry_at && (disableTask.status === 'RETRYING' || disableTask.status === 'FAILED') && (
                <div style={{ marginTop: '4px' }}>
                  <RetryCountdown nextRetryAt={disableTask.next_retry_at} />
                </div>
              )}
              {(disableTask.status === 'FAILED' || disableTask.status === 'RETRYING') && (
                <button onClick={() => onRetry(disableTask.id)} style={{ marginTop: '8px', fontSize: '12px', padding: '4px 10px', background: 'var(--accent-crimson)', color: 'white', border: 'none', borderRadius: '3px', cursor: 'pointer' }}>
                  Повторить сейчас
                </button>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="drawer-panel__footer">
          {canDisable && !disableTask && (
            <button
              onClick={() => onDisable(ad.fb_ad_id)}
              className="btn-disable-inline"
              style={{ backgroundColor: 'var(--accent-crimson)', color: 'white' }}
            >
              Отключить
            </button>
          )}
          {showRetry && (
            <button
              onClick={() => onRetry(disableTask.id)}
              className="btn-disable-inline"
              style={{ backgroundColor: 'var(--accent-gold)' }}
            >
              Повторить
            </button>
          )}
          <button onClick={onClose} className="btn-secondary">
            Закрыть
          </button>
        </div>
      </div>
    </>
  );
}

export function DrawerPanel(props) {
  return ReactDOM.createPortal(<DrawerContent {...props} />, document.body);
}
