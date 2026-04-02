// Dashboard scorecard: статистика и распределение состояний
export function CampaignScorecard({ stats = null, performance = null, spendHistory = [], onStateClick }) {
  if (!stats) {
    return (
      <div className="scorecard scorecard--empty">
        <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
          Нет данных
        </div>
      </div>
    );
  }

  const total = stats.total_ads_monitored || 1;

  const stateDistribution = [
    { label: 'Норма', count: stats.total_ads_monitored - (stats.ads_in_early_signal + stats.ads_in_warning + stats.ads_in_stop + stats.ads_claimed + stats.ads_disabled), color: 'var(--accent-emerald)', state: 'NORMAL' },
    { label: 'Ранний', count: stats.ads_in_early_signal, color: 'var(--accent-orchid)', state: 'EARLY_SIGNAL_SENT' },
    { label: 'Warning', count: stats.ads_in_warning, color: 'var(--accent-gold)', state: 'WARNING_SENT' },
    { label: 'Стоп', count: stats.ads_in_stop, color: 'var(--accent-crimson)', state: 'STOP_SENT' },
    { label: 'Откл.', count: stats.ads_disabled, color: 'var(--accent-slate)', state: 'DISABLED' },
  ];

  return (
    <div className="scorecard">
      {/* Распределение состояний */}
      <div className="state-dist">
        <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
          Распределение
        </h3>
        {stateDistribution.map((row) => {
          const barWidth = row.count > 0 ? Math.max(4, (row.count / total) * 100) : 0;
          return (
            <div
              key={row.state}
              onClick={() => onStateClick?.(row.state)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                marginBottom: '8px',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: '12px', minWidth: '50px' }}>{row.label}</div>
              <div
                style={{
                  flex: 1,
                  height: '20px',
                  backgroundColor: 'var(--border-color)',
                  borderRadius: '3px',
                  overflow: 'hidden',
                }}
              >
                {row.count > 0 && (
                  <div
                    style={{
                      width: `${barWidth}%`,
                      height: '100%',
                      backgroundColor: row.color,
                    }}
                  />
                )}
              </div>
              <div style={{ fontSize: '12px', fontWeight: 600, minWidth: '24px', textAlign: 'right' }}>
                {row.count}
              </div>
            </div>
          );
        })}
      </div>

    </div>
  );
}

function MetricLine({ label, value, color = 'var(--text-primary)' }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', marginBottom: '4px' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontWeight: 600, color }}>{value}</span>
    </div>
  );
}

const STEP_COLORS = ['#4f6ef7', '#a855f7', '#f5a623', '#34d399', '#f74f4f'];

export function FunnelChart({ funnel }) {
  if (!funnel?.length) return null;

  const maxCount = Math.max(...funnel.map((s) => s.count ?? 0), 1);

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
        <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
          Воронка конверсий
        </h3>
      </div>
      <div style={{ padding: '14px 16px' }}>

      {funnel.map((step, i) => {
        const count = step.count ?? 0;
        const barWidth = count > 0 ? Math.max((count / maxCount) * 100, 3) : 0;
        const prevCount = i > 0 ? (funnel[i - 1].count ?? 0) : null;
        const convRate = prevCount != null && prevCount > 0 ? (count / prevCount) * 100 : null;

        const convColor = convRate == null
          ? '#94a3b8'
          : convRate >= 30 ? '#34d399'
          : convRate >= 10 ? '#f5a623'
          : convRate > 0  ? '#f74f4f'
          : '#94a3b8';

        const barColor = STEP_COLORS[i % STEP_COLORS.length];

        return (
          <div key={step.key ?? step.label}>
            {/* Конверсия между шагами */}
            {i > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', paddingLeft: '56px', margin: '3px 0', gap: '8px' }}>
                {/* вертикальная линия-коннектор */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '10px', flexShrink: 0 }}>
                  <div style={{ width: '2px', height: '7px', background: `${convColor}66`, borderRadius: '1px' }} />
                  <div style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderTop: `5px solid ${convColor}99` }} />
                </div>
                {/* бейдж конверсии */}
                <div style={{
                  display: 'inline-flex', alignItems: 'center', gap: '5px',
                  fontSize: '11px', fontWeight: 700,
                  fontFamily: 'JetBrains Mono, monospace',
                  color: convColor,
                  background: `${convColor}25`,
                  border: `1px solid ${convColor}66`,
                  padding: '2px 8px 2px 6px',
                  borderRadius: '4px',
                }}>
                  <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: convColor, flexShrink: 0, display: 'inline-block' }} />
                  {convRate != null ? `${convRate.toFixed(1)}%` : '—'}
                </div>
                <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>конверсия</span>
              </div>
            )}

            {/* Строка шага */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{
                fontSize: '11px', color: 'var(--text-muted)',
                minWidth: '48px', textAlign: 'right', lineHeight: '20px',
              }}>
                {step.label}
              </div>
              <div style={{
                flex: 1, height: '20px',
                background: 'var(--border-color)',
                borderRadius: '3px', overflow: 'hidden',
              }}>
                {count > 0 && (
                  <div style={{
                    width: `${barWidth}%`, height: '100%',
                    background: barColor,
                    transition: 'width 0.4s ease',
                  }} />
                )}
              </div>
              <div style={{
                fontSize: '12px', fontWeight: 700,
                fontFamily: 'JetBrains Mono, monospace',
                color: 'var(--text-primary)',
                minWidth: '30px', textAlign: 'right',
              }}>
                {count}
              </div>
            </div>
          </div>
        );
      })}
      </div>
    </div>
  );
}
