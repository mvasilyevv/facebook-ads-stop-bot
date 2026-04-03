// Перекрут бюджета: факт vs ожидание

const STATUS_STYLES = {
  OVER: { badge: 'badge-danger', text: 'text-danger', label: 'ПЕРЕКРУТ' },
  ON_TARGET: { badge: 'badge-success', text: 'text-success', label: 'НОРМА' },
  UNDER: { badge: 'badge-neutral', text: 'text-secondary', label: 'НЕДОКРУТ' },
};

const fmt$ = (v) => `$${Math.abs(v).toFixed(2)}`;

export function BudgetOverrunChart({ data = [] }) {
  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">Перекрут бюджета (сегодня)</h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных по бюджету</div>
      </div>
    );
  }

  const rows = data.map((d) => ({
    campaign: String(d.campaign_full || d.campaign || ''),
    actual: Number(d.actual_spend) || 0,
    ideal: Number(d.ideal_spend) || 0,
    delta: Number(d.budget_delta_amount) || 0,
    pct: Number(d.budget_delta_percent) || 0,
    status: d.budget_status,
    affectedAds: Number(d.affected_ads) || 0,
    totalAds: Number(d.total_ads) || 0,
  }));

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Перекрут бюджета (сегодня)
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="th-sortable px-3 py-2 text-left">Кампания</th>
              <th className="th-sortable px-3 py-2 text-right">Факт</th>
              <th className="th-sortable px-3 py-2 text-right">Ожидание</th>
              <th className="th-sortable px-3 py-2 text-right">Отклонение</th>
              <th className="th-sortable px-3 py-2 text-center">Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const sts = STATUS_STYLES[row.status] || STATUS_STYLES.ON_TARGET;
              return (
                <tr key={i} className="tr-hover border-b border-border">
                  <td className="max-w-[280px] truncate px-3 py-2.5 text-primary" title={row.campaign}>
                    {row.campaign}
                    {row.affectedAds > 0 && (
                      <span className="ml-2 text-2xs text-muted">{row.affectedAds}/{row.totalAds}</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono font-semibold text-primary">{fmt$(row.actual)}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-muted">{fmt$(row.ideal)}</td>
                  <td className={`px-3 py-2.5 text-right font-mono font-bold ${sts.text}`}>
                    {row.delta >= 0 ? '+' : '−'}{fmt$(row.delta)} ({row.pct > 0 ? '+' : ''}{row.pct.toFixed(1)}%)
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    <span className={sts.badge}>{sts.label}</span>
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
