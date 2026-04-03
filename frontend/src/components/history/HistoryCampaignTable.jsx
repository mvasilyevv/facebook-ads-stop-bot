// Таблица кампаний с сортировкой и drill-down
import { useTableSort, sortRows } from '../../hooks/useTableSort.js';
import { SortableHeader } from '../SortableHeader.jsx';

const TEXT_KEYS = new Set(['campaign_name']);

const COLUMNS = [
  { key: 'campaign_name', label: 'Кампания', align: 'text-left', mono: false },
  { key: 'total_spend', label: 'Расход', align: 'text-right', mono: true, fmt: (v) => `$${Number(v || 0).toFixed(2)}` },
  { key: 'total_leads', label: 'Лиды', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'total_registrations', label: 'Реги', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'total_deposits', label: 'Депозиты', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'avg_cpl', label: 'CPL', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'roas', label: 'ROAS', align: 'text-right', mono: true, fmt: (v) => v != null ? `${Number(v).toFixed(2)}x` : '—' },
];

function CampaignRow({ row, onSelect }) {
  return (
    <tr
      className="tr-hover border-b border-border cursor-pointer"
      onClick={() => onSelect?.(row.campaign_name)}
    >
      {COLUMNS.map((col) => {
        const isRoasLow = col.key === 'roas' && row.roas != null && Number(row.roas) < 1;
        return (
          <td
            key={col.key}
            className={`px-3 py-2.5 ${col.align} ${col.mono ? 'font-mono' : ''} ${
              col.key === 'campaign_name' ? 'max-w-[280px] truncate' : ''
            } ${isRoasLow ? 'text-danger' : 'text-primary'}`}
            title={col.key === 'campaign_name' ? row.campaign_name : undefined}
          >
            {col.fmt ? col.fmt(row[col.key]) : (row[col.key] ?? '—')}
          </td>
        );
      })}
    </tr>
  );
}

export function HistoryCampaignTable({ data = [], onSelect }) {
  const { sortKey, sortDir, handleSort } = useTableSort('total_spend');

  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Кампании
        </h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных</div>
      </div>
    );
  }

  const sorted = sortRows(data, sortKey, sortDir, TEXT_KEYS);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Кампании
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
              <CampaignRow key={i} row={row} onSelect={onSelect} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
