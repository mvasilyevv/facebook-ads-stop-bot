// Таблица топ объявлений по расходу с детальными метриками и статусом
export function TopAdsQualityTable({ data = [] }) {
  if (!data || data.length === 0) {
    return null;
  }

  const stateConfig = {
    NORMAL: { bg: 'var(--accent-emerald-dim)', color: 'var(--accent-emerald)', label: 'Норма' },
    EARLY_SIGNAL_SENT: { bg: 'var(--accent-orchid-dim)', color: 'var(--accent-orchid)', label: 'Ранний' },
    WARNING_SENT: { bg: 'var(--accent-gold-dim)', color: 'var(--accent-gold)', label: 'Warning' },
    STOP_SENT: { bg: 'var(--accent-crimson-dim)', color: 'var(--accent-crimson)', label: 'Стоп' },
    DISABLED: { bg: 'var(--bg-raised)', color: 'var(--text-muted)', label: 'Откл.' },
  };

  const getStateConfig = (state) => stateConfig[state] || stateConfig.NORMAL;

  const formatCurrency = (value) => `$${Number(value || 0).toFixed(2)}`;

  const depositsColor = (leads, deposits) => {
    if (deposits > 0) return 'var(--accent-emerald)';
    if (leads > 0) return 'var(--accent-crimson)';
    return 'var(--text-primary)';
  };

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
      marginBottom: '16px',
      overflow: 'hidden',
    }}>
      {/* Header strip */}
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
      }}>
        <h3 style={{
          margin: 0,
          fontSize: '13px',
          fontWeight: 700,
          textTransform: 'uppercase',
          color: 'var(--text-muted)',
          letterSpacing: '0.06em',
        }}>
          Топ объявления по расходу
        </h3>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: '12px',
        }}>
          <thead>
            <tr style={{ background: 'var(--bg-raised)' }}>
              <th style={{
                padding: '8px 12px',
                textAlign: 'left',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Объявление
              </th>
              <th style={{
                padding: '8px 12px',
                textAlign: 'right',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Расход
              </th>
              <th style={{
                padding: '8px 12px',
                textAlign: 'right',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Клики
              </th>
              <th style={{
                padding: '8px 12px',
                textAlign: 'right',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Лиды
              </th>
              <th style={{
                padding: '8px 12px',
                textAlign: 'right',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Депозиты
              </th>
              <th style={{
                padding: '8px 12px',
                textAlign: 'center',
                fontWeight: 600,
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--text-muted)',
                letterSpacing: '0.06em',
              }}>
                Статус
              </th>
            </tr>
          </thead>
          <tbody>
            {data.slice(0, 10).map((row) => {
              const stateInfo = getStateConfig(row.state);
              const depositsTextColor = depositsColor(row.leads, row.deposits);
              return (
                <tr
                  key={row.fb_ad_id}
                  style={{
                    borderBottom: '1px solid var(--border-dim)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = 'var(--bg-raised)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = 'transparent';
                  }}
                >
                  {/* Объявление */}
                  <td style={{
                    padding: '9px 12px',
                    color: 'var(--text-primary)',
                    fontSize: '12px',
                  }}>
                    <div
                      title={row.name_full}
                      style={{
                        maxWidth: '200px',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        cursor: 'default',
                      }}
                    >
                      {row.name}
                    </div>
                  </td>

                  {/* Расход */}
                  <td style={{
                    padding: '9px 12px',
                    textAlign: 'right',
                    color: 'var(--text-primary)',
                    fontFamily: 'JetBrains Mono, monospace',
                    fontVariantNumeric: 'tabular-nums',
                  }}>
                    {formatCurrency(row.spend)}
                  </td>

                  {/* Клики */}
                  <td style={{
                    padding: '9px 12px',
                    textAlign: 'right',
                    color: 'var(--text-primary)',
                    fontFamily: 'JetBrains Mono, monospace',
                    fontVariantNumeric: 'tabular-nums',
                  }}>
                    {row.clicks}
                  </td>

                  {/* Лиды */}
                  <td style={{
                    padding: '9px 12px',
                    textAlign: 'right',
                    color: row.leads > 0 ? 'var(--accent-emerald)' : 'var(--text-primary)',
                    fontFamily: 'JetBrains Mono, monospace',
                    fontVariantNumeric: 'tabular-nums',
                    fontWeight: row.leads > 0 ? 600 : 400,
                  }}>
                    {row.leads}
                  </td>

                  {/* Депозиты */}
                  <td style={{
                    padding: '9px 12px',
                    textAlign: 'right',
                    color: depositsTextColor,
                    fontFamily: 'JetBrains Mono, monospace',
                    fontVariantNumeric: 'tabular-nums',
                    fontWeight: row.deposits > 0 || row.leads > 0 ? 600 : 400,
                  }}>
                    {row.deposits}
                  </td>

                  {/* Статус */}
                  <td style={{
                    padding: '9px 12px',
                    textAlign: 'center',
                  }}>
                    <span
                      style={{
                        display: 'inline-block',
                        padding: '3px 8px',
                        borderRadius: '3px',
                        fontSize: '10px',
                        fontWeight: 600,
                        backgroundColor: stateInfo.bg,
                        color: stateInfo.color,
                      }}
                    >
                      {stateInfo.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
