// Перекрут бюджета: delta bar chart

const STATUS_STYLES = {
  OVER: { badge: 'badge-danger', text: 'text-danger', label: 'ПЕРЕКРУТ', barColor: 'bg-danger' },
  ON_TARGET: { badge: 'badge-success', text: 'text-success', label: 'НОРМА', barColor: 'bg-success' },
  UNDER: { badge: 'badge-neutral', text: 'text-secondary', label: 'НЕДОКРУТ', barColor: 'bg-neutral' },
};

const fmtDelta = (delta, pct) => {
  const sign = delta >= 0 ? '+' : '−';
  const pctSign = pct > 0 ? '+' : pct < 0 ? '−' : '';
  return `${sign}$${Math.abs(delta).toFixed(2)} (${pctSign}${Math.abs(pct).toFixed(1)}%)`;
};

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
    delta: Number(d.budget_delta_amount) || 0,
    pct: Number(d.budget_delta_percent) || 0,
    status: d.budget_status || 'ON_TARGET',
    affectedAds: Number(d.affected_ads) || 0,
    totalAds: Number(d.total_ads) || 0,
  }));

  // Максимальное абсолютное отклонение — нормировочная база для ширины баров
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.delta)), 0.01);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Перекрут бюджета (сегодня)
      </h3>
      <div className="flex flex-col gap-2">
        {rows.map((row, i) => {
          const sts = STATUS_STYLES[row.status] || STATUS_STYLES.ON_TARGET;
          // Ширина бара: 0–50% от центра (центр = 50% контейнера)
          const barWidthPct = Math.min((Math.abs(row.delta) / maxAbs) * 50, 50);
          const isOver = row.delta > 0;

          return (
            <div key={i} className="rounded-md px-3 py-2 hover:bg-elevated/60 transition-colors">
              {/* Строка 1: название + бейдж */}
              <div className="flex items-center justify-between mb-1.5">
                <span
                  className="truncate max-w-[55%] text-sm text-primary"
                  title={row.campaign}
                >
                  {row.campaign}
                  {row.affectedAds > 0 && (
                    <span className="ml-1.5 text-2xs text-muted">{row.affectedAds}/{row.totalAds}</span>
                  )}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`font-mono text-xs font-bold ${sts.text}`}>
                    {fmtDelta(row.delta, row.pct)}
                  </span>
                  <span className={sts.badge}>{sts.label}</span>
                </div>
              </div>

              {/* Строка 2: delta bar с центром */}
              <div className="relative h-2 flex items-center">
                {/* Фоновая дорожка */}
                <div className="absolute inset-0 rounded-full bg-border opacity-40" />
                {/* Центральная метка */}
                <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border z-10" />
                {/* Бар */}
                {isOver ? (
                  // Перекрут: бар вправо от центра
                  <div
                    className={`absolute top-0 bottom-0 rounded-r-full ${sts.barColor} opacity-80`}
                    style={{ left: '50%', width: `${barWidthPct}%` }}
                  />
                ) : (
                  // Недокрут / норма: бар влево от центра
                  <div
                    className={`absolute top-0 bottom-0 rounded-l-full ${sts.barColor} opacity-60`}
                    style={{ right: '50%', width: `${barWidthPct}%` }}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
