// Счётчик нарушений правил — компактный список вместо чарта
export function RuleViolationRanking({ data = [] }) {
  if (!data.length) return null;

  const badgeColor = (index) => {
    if (index === 0) return { bg: 'var(--accent-crimson-dim)', color: 'var(--accent-crimson)' };
    if (index === 1) return { bg: 'var(--accent-gold-dim)', color: 'var(--accent-gold)' };
    return { bg: 'var(--accent-teal-dim)', color: 'var(--accent-teal)' };
  };

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
      marginBottom: '16px',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
        fontSize: '13px',
        fontWeight: 700,
        textTransform: 'uppercase',
        color: 'var(--text-muted)',
        letterSpacing: '0.06em',
      }}>
        Нарушения правил
      </div>

      <div style={{ padding: '8px 0' }}>
        {data.map((item, i) => {
          const { bg, color } = badgeColor(i);
          return (
            <div key={item.rule ?? i} style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '7px 16px',
              borderBottom: i < data.length - 1 ? '1px solid var(--border-dim)' : 'none',
            }}>
              <span style={{
                fontSize: '10px',
                fontWeight: 700,
                color: 'var(--text-muted)',
                minWidth: '14px',
                textAlign: 'right',
              }}>
                {i + 1}
              </span>
              <span style={{
                flex: 1,
                fontSize: '12px',
                color: 'var(--text-secondary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {item.rule || item.rule_short || '—'}
              </span>
              <span style={{
                background: bg,
                color,
                borderRadius: '3px',
                padding: '2px 7px',
                fontSize: '11px',
                fontWeight: 700,
                fontFamily: 'JetBrains Mono, monospace',
                flexShrink: 0,
              }}>
                {item.count}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
