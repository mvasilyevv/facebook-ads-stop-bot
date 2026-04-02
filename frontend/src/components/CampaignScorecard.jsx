import { ResponsiveContainer, LineChart, Line } from 'recharts';

// Dashboard scorecard: статистика и распределение состояний
export function CampaignScorecard({ stats = null, performance = null, spendHistory = [], onStateClick }) {
  if (!stats || !performance) {
    return (
      <div className="scorecard scorecard--empty">
        <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
          Нет данных
        </div>
      </div>
    );
  }

  const total = stats.total_ads_monitored || 1;
  const fmt$ = (v) => (v != null ? `$${Number(v).toFixed(2)}` : '—');
  const fmtN = (v) => (v != null ? String(v) : '—');

  const stateDistribution = [
    { label: 'Норма', count: stats.total_ads_monitored - (stats.ads_in_early_signal + stats.ads_in_warning + stats.ads_in_stop + stats.ads_claimed + stats.ads_disabled), color: 'var(--accent-teal)', state: 'NORMAL' },
    { label: 'Ранний', count: stats.ads_in_early_signal, color: 'var(--accent-orchid)', state: 'EARLY_SIGNAL_SENT' },
    { label: 'Warning', count: stats.ads_in_warning, color: 'var(--accent-gold)', state: 'WARNING_SENT' },
    { label: 'Стоп', count: stats.ads_in_stop, color: 'var(--accent-crimson)', state: 'STOP_SENT' },
    { label: 'Откл.', count: stats.ads_disabled, color: 'var(--accent-slate)', state: 'DISABLED' },
  ];

  return (
    <div className="scorecard">
      {/* Section A: Metrics */}
      <div className="scorecard__section">
        <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          Сегодня
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '12px' }}>
          <MetricLine label="Расход" value={fmt$(performance.summary?.spend)} />
          <MetricLine label="Лиды" value={fmtN(performance.summary?.leads)} color={performance.summary?.leads > 0 ? 'var(--accent-teal)' : 'var(--text-muted)'} />
          <MetricLine label="Реги" value={fmtN(performance.summary?.registrations)} />
          <MetricLine label="Депозиты" value={fmtN(performance.summary?.deposits)} color={performance.summary?.deposits > 0 ? 'var(--accent-teal)' : (performance.summary?.spend > 0 ? 'var(--accent-crimson)' : 'var(--text-muted)')} />
          <MetricLine label="CPR" value={fmt$(performance.summary?.cpr)} />
          <MetricLine label="Рег→Деп" value={`${Number(performance.summary?.reg_to_dep_rate ?? 0).toFixed(1)}%`} />
        </div>
      </div>

      {/* Section B: State Distribution */}
      <div className="state-dist">
        <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
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
                  backgroundColor: 'var(--bg-secondary)',
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

      {/* Section C: Funnel */}
      {performance?.funnel?.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px' }}>Воронка</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', flexWrap: 'wrap' }}>
            {performance.funnel.map((step, i) => (
              <div key={step.key} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{step.count ?? '—'}</div>
                  <div style={{ color: 'var(--text-muted)' }}>{step.label}</div>
                  {step.conversion_rate != null && i > 0 && (
                    <div style={{ fontSize: '10px', color: 'var(--accent-orchid)' }}>{(step.conversion_rate * 100).toFixed(1)}%</div>
                  )}
                </div>
                {i < performance.funnel.length - 1 && <span style={{ color: 'var(--text-muted)' }}>→</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Section D: Spend sparkline */}
      {spendHistory?.length > 1 && (
        <div style={{ marginTop: '12px' }}>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Расход 24ч</div>
          <ResponsiveContainer width="100%" height={56}>
            <LineChart data={spendHistory} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
              <Line type="monotone" dataKey="spend" stroke="var(--accent-teal)" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="deposits" stroke="var(--accent-crimson)" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
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
