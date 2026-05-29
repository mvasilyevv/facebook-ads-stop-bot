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

function OfferCard({ row }) {
  return (
    <div className="rounded-md border border-border bg-elevated/35 px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="font-mono text-sm font-semibold text-primary">{row.offer_code || '—'}</span>
        <span className={`font-mono text-sm font-semibold ${profitColor(row.profit)}`}>
          {row.profit != null ? `$${Number(row.profit).toFixed(2)}` : '—'}
        </span>
      </div>
      <div className="grid grid-cols-4 gap-2 text-2xs">
        <span className="text-muted">Расход <b className="font-mono text-primary">${Number(row.total_spend || 0).toFixed(2)}</b></span>
        <span className="text-muted">Реги <b className="font-mono text-primary">{row.total_registrations || 0}</b></span>
        <span className="text-muted">Деп <b className={`font-mono ${Number(row.total_deposits) > 0 ? 'text-success' : 'text-primary'}`}>{row.total_deposits || 0}</b></span>
        <span className="text-muted">ROAS <b className={`font-mono ${row.roas != null && Number(row.roas) < 1 ? 'text-danger' : 'text-primary'}`}>{row.roas != null ? `${Number(row.roas).toFixed(2)}x` : '—'}</b></span>
      </div>
    </div>
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
      <div className="grid gap-2 md:hidden">
        {sorted.map((row, i) => (
          <OfferCard key={row.offer_code || i} row={row} />
        ))}
      </div>
      <div className="hidden overflow-x-auto md:block">
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
