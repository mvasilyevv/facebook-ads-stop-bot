import { useEffect } from 'react';
import ReactDOM from 'react-dom';
import { StateIcon } from './StateIcon';

// Форматирование значений
const fmt$ = (v) => v != null ? `$${Number(v).toFixed(2)}` : '—';
const fmtN = (v) => v != null ? String(v) : '—';
const fmtPct = (v) => v != null ? `${Number(v).toFixed(2)}%` : '—';

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
            <div style={{ marginTop: '16px', padding: '8px', backgroundColor: 'rgba(0, 0, 0, 0.02)', borderRadius: '4px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '4px' }}>
                Статус отключения
              </div>
              <div style={{ fontSize: '13px' }}>
                {disableTask.status === 'PENDING' && '⏳ Ожидает выполнения'}
                {disableTask.status === 'SUCCESS' && '✅ Объявление отключено'}
                {disableTask.status === 'FAILED' && (
                  <>
                    <div style={{ color: 'var(--accent-crimson)' }}>❌ Ошибка отключения</div>
                    {disableTask.error && <div style={{ fontSize: '11px', marginTop: '4px', color: 'var(--text-muted)' }}>{disableTask.error}</div>}
                  </>
                )}
              </div>
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
