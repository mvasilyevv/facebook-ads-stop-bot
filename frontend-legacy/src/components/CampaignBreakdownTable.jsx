// Разбивка по кампаниям с сортировкой
import { useTableSort, sortRows } from '../hooks/useTableSort.js';
import { SortableHeader } from './SortableHeader.jsx';

const TEXT_KEYS = new Set(['campaign']);

const COLUMNS = [
  { key: 'campaign', label: 'Кампания', align: 'text-left', mono: false },
  { key: 'spend', label: 'Расход', align: 'text-right', mono: true, fmt: (v) => `$${Number(v || 0).toFixed(2)}` },
  { key: 'leads', label: 'Лиды', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'registrations', label: 'Реги', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'deposits', label: 'Депозиты', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'cpr', label: 'CPR', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'reg_to_dep_rate', label: 'Конв%', align: 'text-right', mono: true, fmt: (v) => v != null ? `${(Number(v) * 100).toFixed(1)}%` : '—' },
];

export function CampaignBreakdownTable({ data = [] }) {
  const { sortKey, sortDir, handleSort } = useTableSort('spend');

  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">Разбивка по кампаниям</h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных по кампаниям</div>
      </div>
    );
  }

  const sorted = sortRows(data, sortKey, sortDir, TEXT_KEYS);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Разбивка по кампаниям
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-elevated/50">
              {COLUMNS.map((col) => (
                <SortableHeader
                  key={col.key}
                  col={col}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr key={i} className="tr-hover border-b border-border">
                {COLUMNS.map((col) => (
                  <td
                    key={col.key}
                    className={`px-3 py-2.5 ${col.align} ${col.mono ? 'font-mono' : ''} ${
                      col.key === 'campaign' ? 'max-w-[280px] truncate' : ''
                    } ${
                      col.key === 'deposits' && Number(row[col.key]) > 0 ? 'font-semibold text-success' : 'text-primary'
                    }`}
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
