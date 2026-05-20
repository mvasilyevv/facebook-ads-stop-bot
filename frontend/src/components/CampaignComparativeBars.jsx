// Эффективность кампаний по CPR — горизонтальные бары

export function CampaignComparativeBars({ data = [] }) {
  const rows = (!data || data.length === 0) ? [] : [...data]
    .filter((item) => parseFloat(item.spend) > 0)
    .sort((a, b) => {
      const cprA = parseFloat(a.cpr);
      const cprB = parseFloat(b.cpr);
      const aKey = Number.isFinite(cprA) && cprA > 0 ? cprA : Number.POSITIVE_INFINITY;
      const bKey = Number.isFinite(cprB) && cprB > 0 ? cprB : Number.POSITIVE_INFINITY;
      return aKey - bKey;
    })
    .slice(0, 8)
    .map((item) => {
      const regs = parseInt(item.registrations, 10) || 0;
      const spend = parseFloat(item.spend) || 0;
      const cprRaw = parseFloat(item.cpr);
      const cpr = Number.isFinite(cprRaw) && cprRaw > 0 ? cprRaw : null;
      return {
        campaign: item.campaign || '',
        label: (item.campaign || '').replace(/\s*\|\s*/g, ' · ').substring(0, 28) +
               ((item.campaign || '').length > 28 ? '…' : ''),
        cpr,
        cprLabel: cpr != null ? `$${cpr.toFixed(2)}` : '—',
        spend,
        deposits: parseInt(item.deposits, 10) || 0,
        leads: parseInt(item.leads, 10) || 0,
        registrations: regs,
      };
    });

  if (rows.length === 0) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">Эффективность кампаний (CPR)</h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных по CPR кампаний</div>
      </div>
    );
  }

  const finiteCprs = rows.map((r) => r.cpr).filter((v) => v != null);
  const maxCpr = Math.max(...finiteCprs, 1);
  const barWidth = (cpr) => (cpr != null ? Math.max(4, (cpr / maxCpr) * 100) : 4);

  const barColor = (row) => {
    if (row.deposits > 0) return 'bg-success';
    if (row.leads > 0) return 'bg-warning';
    return 'bg-neutral';
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-2xs font-bold uppercase tracking-widest text-muted">
          Эффективность кампаний (CPR)
        </h3>
        <div className="flex gap-3 text-2xs text-muted">
          <span className="flex items-center gap-1">
            <span className="inline-block h-[7px] w-[7px] rounded-full bg-success" />
            есть депозиты
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-[7px] w-[7px] rounded-full bg-warning" />
            нет депозитов
          </span>
        </div>
      </div>

      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.campaign} className="flex items-center gap-3">
            <div
              className="w-[120px] flex-shrink-0 truncate text-right text-2xs text-muted"
              title={row.campaign}
            >
              {row.label}
            </div>
            <div className="h-[18px] flex-1 overflow-hidden rounded-sm bg-elevated">
              <div
                className={`h-full rounded-sm opacity-85 transition-all duration-300 ${barColor(row)}`}
                style={{ width: `${barWidth(row.cpr)}%` }}
              />
            </div>
            <div className="flex flex-shrink-0 items-center gap-1.5" style={{ minWidth: '80px' }}>
              <span className="font-mono text-2xs font-semibold text-secondary">
                {row.cprLabel}
              </span>
              {row.registrations === 0 && row.spend > 0 && (
                <span className="text-[10px] text-muted">CPR ∞</span>
              )}
              {row.deposits > 0 && (
                <span className="badge-success text-[10px]">
                  {row.deposits} деп.
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[10px] text-muted">
        Длина бара — фактический CPR (короче = дешевле регистрация). Без регистраций — «—» / CPR ∞.
      </div>
    </div>
  );
}
