import { StateIcon } from './StateIcon.jsx';

const fmt$ = (v) => `$${Number(v || 0).toFixed(2)}`;

/** Таблица топ объявлений по расходу */
export function TopAdsQualityTable({ data = [] }) {
  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Топ объявления по расходу
      </h3>
      {(!data || data.length === 0) && (
        <div className="py-4 text-center text-sm text-muted">Нет данных по объявлениям</div>
      )}
      {data && data.length > 0 && <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-elevated/50">
              <th className="th-sortable px-3 py-2 text-left">Объявление</th>
              <th className="th-sortable px-3 py-2 text-right">Расход</th>
              <th className="th-sortable px-3 py-2 text-right">Клики</th>
              <th className="th-sortable px-3 py-2 text-right">Лиды</th>
              <th className="th-sortable px-3 py-2 text-right">Депозиты</th>
              <th className="th-sortable px-3 py-2 text-center">Статус</th>
            </tr>
          </thead>
          <tbody>
            {data.slice(0, 10).map((row) => (
              <tr key={row.fb_ad_id} className="tr-hover border-b border-border">
                <td className="max-w-[200px] truncate px-3 py-2.5 text-primary" title={row.name_full}>
                  {row.name}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-primary">{fmt$(row.spend)}</td>
                <td className="px-3 py-2.5 text-right font-mono text-primary">{row.clicks}</td>
                <td className={`px-3 py-2.5 text-right font-mono ${row.leads > 0 ? 'font-semibold text-success' : 'text-primary'}`}>
                  {row.leads}
                </td>
                <td className={`px-3 py-2.5 text-right font-mono ${row.deposits > 0 ? 'font-semibold text-success' : row.leads > 0 ? 'font-semibold text-danger' : 'text-primary'}`}>
                  {row.deposits}
                </td>
                <td className="px-3 py-2.5 text-center">
                  <StateIcon state={row.state} size="sm" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>}
    </div>
  );
}
