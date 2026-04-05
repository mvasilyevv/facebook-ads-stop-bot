// Таблица офферов с сортировкой
import { useTableSort, sortRows } from '../../hooks/useTableSort.js';
import { SortableHeader } from '../SortableHeader.jsx';

const TEXT_KEYS = new Set(['offer_code']);

const COLUMNS = [
  { key: 'offer_code', label: 'Оффер', align: 'text-left', mono: false },
  { key: 'total_spend', label: 'Расход', align: 'text-right', mono: true, fmt: (v) => `$${Number(v || 0).toFixed(2)}` },
  { key: 'total_registrations', label: 'Реги', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'total_deposits', label: 'Депозиты', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'avg_cpr', label: 'CPR', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'avg_cost_per_deposit', label: 'Spend/Dep', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'roas', label: 'ROAS', align: 'text-right', mono: true, fmt: (v) => v != null ? `${Number(v).toFixed(2)}x` : '—' },
  { key: 'profit', label: 'Profit', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
];

function profitColor(val) {
  if (val == null) return 'text-primary';
  return Number(val) > 0 ? 'text-success' : Number(val) < 0 ? 'text-danger' : 'text-primary';
}

function cellColor(col, row) {
  if (col.key === 'profit') return profitColor(row['profit']);
  if (col.key === 'roas' && row['roas'] != null && Number(row['roas']) < 1) return 'text-danger';
  return 'text-primary';
}

function OfferRow({ row }) {
  return (
    <tr className="tr-hover border-b border-border">
      {COLUMNS.map((col) => (
        <td
          key={col.key}
          className={`px-3 py-2.5 ${col.align} ${col.mono ? 'font-mono' : ''} ${cellColor(col, row)}`}
        >
          {col.fmt ? col.fmt(row[col.key]) : (row[col.key] ?? '—')}
        </td>
      ))}
    </tr>
  );
}

export function HistoryOffersTable({ data = [] }) {
  const { sortKey, sortDir, handleSort } = useTableSort('total_spend');

  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Офферы
        </h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных</div>
      </div>
    );
  }

  const sorted = sortRows(data, sortKey, sortDir, TEXT_KEYS);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Офферы
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
              <OfferRow key={row.offer_code || i} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
