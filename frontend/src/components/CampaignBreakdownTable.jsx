// Таблица разбивки по кампаниям с сортировкой
import { useState } from 'react';

const COLUMNS = [
  { key: 'campaign', label: 'Кампания', align: 'left', mono: false },
  { key: 'spend', label: 'Расход', align: 'right', mono: true, fmt: (v) => `$${Number(v || 0).toFixed(2)}` },
  { key: 'leads', label: 'Лиды', align: 'right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'registrations', label: 'Реги', align: 'right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'deposits', label: 'Депозиты', align: 'right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'cpr', label: 'CPR', align: 'right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'reg_to_dep_rate', label: 'Конв%', align: 'right', mono: true, fmt: (v) => v != null ? `${(Number(v) * 100).toFixed(1)}%` : '—' },
];

export function CampaignBreakdownTable({ data = [] }) {
  const [sortKey, setSortKey] = useState('spend');
  const [sortDir, setSortDir] = useState('desc');

  if (!data.length) return null;

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sorted = [...data].sort((a, b) => {
    const av = a[sortKey] ?? 0;
    const bv = b[sortKey] ?? 0;
    if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      marginBottom: '16px',
      boxShadow: 'var(--shadow-sm)',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-color)' }}>
        <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
          Разбивка по кампаниям
        </h3>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-raised)' }}>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  style={{
                    padding: '8px 12px',
                    textAlign: col.align,
                    fontWeight: 600,
                    fontSize: '11px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    color: sortKey === col.key ? 'var(--accent-teal)' : 'var(--text-muted)',
                    cursor: 'pointer',
                    userSelect: 'none',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span style={{ marginLeft: '4px' }}>{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr
                key={i}
                style={{
                  borderBottom: '1px solid var(--border-dim)',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-raised)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = ''; }}
              >
                {COLUMNS.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      padding: '9px 12px',
                      textAlign: col.align,
                      color: col.key === 'deposits' && Number(row[col.key]) > 0 ? 'var(--accent-emerald)' : 'var(--text-primary)',
                      fontFamily: col.mono ? 'JetBrains Mono, monospace' : 'inherit',
                      fontVariantNumeric: col.mono ? 'tabular-nums' : undefined,
                      maxWidth: col.key === 'campaign' ? '280px' : undefined,
                      overflow: col.key === 'campaign' ? 'hidden' : undefined,
                      textOverflow: col.key === 'campaign' ? 'ellipsis' : undefined,
                      whiteSpace: col.key === 'campaign' ? 'nowrap' : undefined,
                    }}
                    title={col.key === 'campaign' ? row[col.key] : undefined}
                  >
                    {col.fmt ? col.fmt(row[col.key]) : (row[col.key] ?? '—')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
