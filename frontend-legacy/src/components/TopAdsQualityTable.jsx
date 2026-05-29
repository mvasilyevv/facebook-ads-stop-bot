import { StateIcon } from './StateIcon.jsx';
import { fmt$ } from '../utils/formatters.js';

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
      {data && data.length > 0 && (
        <>
          <div className="grid gap-2 md:hidden">
            {data.slice(0, 10).map((row) => (
              <div key={row.fb_ad_id} className="rounded-md border border-border bg-elevated/35 px-3 py-2.5">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-primary" title={row.name_full}>{row.name}</div>
                    <div className="mt-0.5 font-mono text-2xs text-secondary">{fmt$(row.spend)}</div>
                  </div>
                  <StateIcon state={row.state} size="sm" />
                </div>
                <div className="grid grid-cols-3 gap-2 text-2xs">
                  <span className="text-muted">Клики <b className="font-mono text-primary">{row.clicks}</b></span>
                  <span className="text-muted">Лиды <b className={`font-mono ${row.leads > 0 ? 'text-success' : 'text-primary'}`}>{row.leads}</b></span>
                  <span className="text-muted">Деп <b className={`font-mono ${row.deposits > 0 ? 'text-success' : row.leads > 0 ? 'text-danger' : 'text-primary'}`}>{row.deposits}</b></span>
                </div>
              </div>
            ))}
          </div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-elevated/50">
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-left">Объявление</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">Расход</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">Клики</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">Лиды</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-right">Депозиты</th>
                  <th className="px-3 py-2 text-2xs uppercase tracking-wider text-muted text-center">Статус</th>
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
          </div>
        </>
      )}
    </div>
  );
}
