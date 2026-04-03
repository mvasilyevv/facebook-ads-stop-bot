// Таблица объявлений за период с сортировкой
import { useTableSort, sortRows } from '../../hooks/useTableSort.js';
import { SortableHeader } from '../SortableHeader.jsx';

const TEXT_KEYS = new Set(['ad_name', 'campaign_name', 'offer_code']);

const COLUMNS = [
  { key: 'ad_name', label: 'Объявление', align: 'text-left', mono: false },
  { key: 'campaign_name', label: 'Кампания', align: 'text-left', mono: false },
  { key: 'offer_code', label: 'Оффер', align: 'text-left', mono: false, fmt: (v) => v || '—' },
  { key: 'total_spend', label: 'Расход', align: 'text-right', mono: true, fmt: (v) => `$${Number(v || 0).toFixed(2)}` },
  { key: 'avg_cpc', label: 'CPC', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'total_leads', label: 'Лиды', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'total_registrations', label: 'Реги', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'avg_cpr', label: 'CPR', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
  { key: 'total_deposits', label: 'Депозиты', align: 'text-right', mono: true, fmt: (v) => String(v || 0) },
  { key: 'avg_spend_per_dep', label: 'Spend/Dep', align: 'text-right', mono: true, fmt: (v) => v != null ? `$${Number(v).toFixed(2)}` : '—' },
];

export function HistoryAdsTable({ data = [] }) {
  const { sortKey, sortDir, handleSort } = useTableSort('total_spend');

  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Объявления
        </h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных по объявлениям</div>
      </div>
    );
  }

  const sorted = sortRows(data, sortKey, sortDir, TEXT_KEYS);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Объявления
        <span className="ml-2 text-muted font-normal normal-case tracking-normal">{data.length}</span>
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
            {sorted.map((row) => {
              const hasDeps = Number(row.total_deposits) > 0;
              const noDepsWithSpend = !hasDeps && Number(row.total_spend) > 0;
              return (
                <tr key={row.fb_ad_id} className="tr-hover border-b border-border">
                  <td className="max-w-[200px] truncate px-3 py-2.5 text-primary" title={row.ad_name}>
                    {row.ad_name}
                  </td>
                  <td className="max-w-[180px] truncate px-3 py-2.5 text-secondary" title={row.campaign_name}>
                    {row.campaign_name}
                  </td>
                  <td className="px-3 py-2.5 text-left text-secondary">
                    {row.offer_code || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    ${Number(row.total_spend || 0).toFixed(2)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    {row.avg_cpc != null ? `$${Number(row.avg_cpc).toFixed(2)}` : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    {row.total_leads || 0}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    {row.total_registrations || 0}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    {row.avg_cpr != null ? `$${Number(row.avg_cpr).toFixed(2)}` : '—'}
                  </td>
                  <td className={`px-3 py-2.5 text-right font-mono font-semibold ${hasDeps ? 'text-success' : noDepsWithSpend ? 'text-danger' : 'text-primary'}`}>
                    {row.total_deposits || 0}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-primary">
                    {row.avg_spend_per_dep != null ? `$${Number(row.avg_spend_per_dep).toFixed(2)}` : '—'}
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
