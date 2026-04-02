// Таблица перекрута бюджета — факт vs ожидание по кампаниям
export function BudgetOverrunChart({ data = [] }) {
  if (!data.length) return null;

  const rows = data.map((d) => ({
    campaign: String(d.campaign_full || d.campaign || ''),
    actual: Number(d.actual_spend) || 0,
    ideal: Number(d.ideal_spend) || 0,
    delta: Number(d.budget_delta_amount) || 0,
    pct: Number(d.budget_delta_percent) || 0,
    status: d.budget_status,
    affectedAds: Number(d.affected_ads) || 0,
    totalAds: Number(d.total_ads) || 0,
  }));

  const statusLabel = { OVER: 'ПЕРЕКРУТ', ON_TARGET: 'НОРМА', UNDER: 'НЕДОКРУТ' };
  const statusColor = {
    OVER: 'var(--accent-crimson)',
    ON_TARGET: 'var(--accent-emerald)',
    UNDER: 'var(--accent-slate)',
  };
  const fmt$ = (v) => `$${Math.abs(v).toFixed(2)}`;
  const fmtPct = (v) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}%`;
  const fmtDelta = (delta, pct) => `${delta >= 0 ? '+' : '−'}${fmt$(delta)} (${fmtPct(pct)})`;

  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-color)', padding: '16px', borderRadius: '6px', marginBottom: '16px', boxShadow: 'var(--shadow-sm)' }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
        Перекрут бюджета — факт vs ожидание (сегодня)
      </h3>

      <div style={{ display: 'grid', gridTemplateColumns: 'auto repeat(4, max-content)', gap: '0 24px', alignItems: 'center' }}>
        {/* Header */}
        {['Кампания', 'Факт', 'Ожидание', 'Отклонение', 'Статус'].map((h) => (
          <div key={h} style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', paddingBottom: '8px', borderBottom: '1px solid var(--border-color)' }}>
            {h}
          </div>
        ))}

        {/* Rows */}
        {rows.map((row, i) => (
          <>
            <div key={`name-${i}`} style={{ fontSize: '13px', padding: '10px 0', borderBottom: '1px solid var(--border-color)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '320px' }} title={row.campaign}>
              {row.campaign}
              {row.affectedAds > 0 && (
                <span style={{ marginLeft: '8px', fontSize: '11px', color: 'var(--text-muted)' }}>
                  {row.affectedAds}/{row.totalAds} объявл.
                </span>
              )}
            </div>
            <div key={`actual-${i}`} style={{ fontSize: '14px', fontWeight: 600, padding: '10px 0', borderBottom: '1px solid var(--border-color)', textAlign: 'right' }}>
              {fmt$(row.actual)}
            </div>
            <div key={`ideal-${i}`} style={{ fontSize: '13px', color: 'var(--text-muted)', padding: '10px 0', borderBottom: '1px solid var(--border-color)', textAlign: 'right' }}>
              {fmt$(row.ideal)}
            </div>
            <div key={`delta-${i}`} style={{ fontSize: '14px', fontWeight: 700, padding: '10px 0', borderBottom: '1px solid var(--border-color)', textAlign: 'right', color: statusColor[row.status] || 'var(--text-primary)' }}>
              {fmtDelta(row.delta, row.pct)}
            </div>
            <div key={`status-${i}`} style={{ padding: '10px 0', borderBottom: '1px solid var(--border-color)' }}>
              <span style={{ fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '3px', background: statusColor[row.status] + '22', color: statusColor[row.status] }}>
                {statusLabel[row.status] || row.status}
              </span>
            </div>
          </>
        ))}
      </div>
    </div>
  );
}
