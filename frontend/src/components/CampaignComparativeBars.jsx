// Эффективность кампаний по CPR — горизонтальные бары (меньше CPR = лучше = бар длиннее)
function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-accent)',
      borderRadius: '4px',
      padding: '10px 14px',
      fontSize: '12px',
      maxWidth: '280px',
      boxShadow: 'var(--shadow-md)',
    }}>
      <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px', lineHeight: 1.3 }}>
        {d.campaign}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
        <span style={{ color: 'var(--accent-teal)' }}>Расход: <b>${d.spend.toFixed(2)}</b></span>
        <span style={{ color: 'var(--text-secondary)' }}>CPR: <b>${d.cpr.toFixed(2)}</b></span>
        <span style={{ color: 'var(--text-secondary)' }}>Лиды: <b>{d.leads}</b></span>
        <span style={{ color: d.deposits > 0 ? 'var(--accent-emerald)' : 'var(--text-muted)' }}>
          Депозиты: <b>{d.deposits}</b>
        </span>
      </div>
    </div>
  );
}

export function CampaignComparativeBars({ data = [] }) {
  if (!data || data.length === 0) return null;

  // Только кампании с CPR (есть лиды)
  const rows = [...data]
    .filter((item) => parseFloat(item.cpr) > 0)
    .sort((a, b) => parseFloat(a.cpr) - parseFloat(b.cpr)) // лучшие сверху
    .slice(0, 8)
    .map((item) => ({
      campaign: item.campaign || '',
      label: (item.campaign || '').replace(/\s*\|\s*/g, ' · ').substring(0, 28) +
             ((item.campaign || '').length > 28 ? '…' : ''),
      cpr: parseFloat(item.cpr),
      spend: parseFloat(item.spend) || 0,
      deposits: parseInt(item.deposits, 10) || 0,
      leads: parseInt(item.leads, 10) || 0,
    }));

  if (rows.length === 0) return null;

  const maxCpr = Math.max(...rows.map((r) => r.cpr));

  // Инвертированная ширина: лучший CPR = самый длинный бар
  const barWidth = (cpr) => Math.max(4, (1 - (cpr - rows[0].cpr) / (maxCpr - rows[0].cpr + 1)) * 100);

  const barColor = (row) => {
    if (row.deposits > 0) return 'var(--accent-emerald)';
    if (row.leads > 0) return 'var(--accent-gold)';
    return 'var(--accent-slate)';
  };

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
          Эффективность кампаний (CPR)
        </span>
        <div style={{ display: 'flex', gap: '12px', fontSize: '11px', color: 'var(--text-muted)' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--accent-emerald)', display: 'inline-block' }} />
            есть депозиты
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--accent-gold)', display: 'inline-block' }} />
            нет депозитов
          </span>
        </div>
      </div>

      <div style={{ padding: '12px 16px' }}>
        {rows.map((row) => (
          <div key={row.campaign} style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
            {/* Название кампании */}
            <div style={{
              fontSize: '11px',
              color: 'var(--text-muted)',
              minWidth: '120px',
              maxWidth: '120px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              textAlign: 'right',
              flexShrink: 0,
            }} title={row.campaign}>
              {row.label}
            </div>

            {/* Бар */}
            <div style={{ flex: 1, height: '18px', background: 'var(--bg-tertiary)', borderRadius: '3px', overflow: 'hidden' }}>
              <div style={{
                width: `${barWidth(row.cpr)}%`,
                height: '100%',
                background: barColor(row),
                borderRadius: '3px',
                opacity: 0.85,
                transition: 'width 0.3s ease',
              }} />
            </div>

            {/* CPR + депозиты */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0, minWidth: '80px' }}>
              <span style={{
                fontSize: '11px',
                fontFamily: 'JetBrains Mono, monospace',
                fontWeight: 600,
                color: 'var(--text-secondary)',
              }}>
                ${row.cpr.toFixed(2)}
              </span>
              {row.deposits > 0 && (
                <span style={{
                  fontSize: '10px',
                  fontWeight: 700,
                  color: 'var(--accent-emerald)',
                  background: 'var(--accent-emerald-dim)',
                  borderRadius: '3px',
                  padding: '1px 5px',
                }}>
                  {row.deposits} деп.
                </span>
              )}
            </div>
          </div>
        ))}
        <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px' }}>
          Длина бара — относительная эффективность. Меньше CPR = лучше.
        </div>
      </div>
    </div>
  );
}
