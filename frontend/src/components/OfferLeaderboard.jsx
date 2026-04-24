/**
 * OfferLeaderboard — топ-5 офферов по депозитам за сегодня.
 * Принимает data: массив кампаний из performance?.campaigns.
 */
export function OfferLeaderboard({ data = [] }) {
  // Группируем кампании по offer_code или по первым двум сегментам названия через |
  const grouped = {};
  for (const row of data) {
    const code =
      row.offer_code ||
      (() => {
        const parts = (row.campaign || '').split('|').map((s) => s.trim());
        return parts.length >= 2 ? parts[1] : parts[0] || '—';
      })();

    if (!grouped[code]) {
      grouped[code] = { code, spend: 0, deposits: 0, leads: 0, registrations: 0 };
    }
    grouped[code].spend         += Number(row.spend         ?? 0);
    grouped[code].deposits      += Number(row.deposits      ?? 0);
    grouped[code].leads         += Number(row.leads         ?? 0);
    grouped[code].registrations += Number(row.registrations ?? 0);
  }

  const offers = Object.values(grouped)
    .sort((a, b) => b.deposits - a.deposits || b.spend - a.spend)
    .slice(0, 5);

  if (offers.length === 0) {
    return (
      <div>
        <p className="text-2xs font-bold uppercase tracking-widest text-secondary mb-3">
          Офферы сегодня
        </p>
        <p className="text-2xs text-muted">Нет данных</p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-2xs font-bold uppercase tracking-widest text-secondary mb-3">
        Офферы сегодня
      </p>
      <div className="space-y-2">
        {offers.map((o) => {
          // Индикатор: зелёный — есть депозиты, жёлтый — есть лиды без депозитов, красный — ни того ни другого при расходе
          const dotColor =
            o.deposits > 0
              ? 'bg-success'
              : o.leads > 0
                ? 'bg-warning'
                : o.spend > 0
                  ? 'bg-danger'
                  : 'bg-zinc-600';

          const depositsColor =
            o.deposits > 0
              ? 'text-success'
              : o.spend > 0
                ? 'text-danger'
                : 'text-muted';

          // Условный ROAS: deposits / spend * 100
          const roas = o.spend > 0 ? ((o.deposits / o.spend) * 100).toFixed(1) : null;

          return (
            <div
              key={o.code}
              className="flex items-center gap-2 text-2xs"
            >
              {/* Цветной индикатор */}
              <span className={`status-dot flex-shrink-0 ${dotColor}`} />

              {/* Код оффера */}
              <span className="font-mono font-bold text-primary w-16 truncate" title={o.code}>
                {o.code}
              </span>

              {/* Расход */}
              <span className="text-muted w-16 text-right">
                ${o.spend.toFixed(2)}
              </span>

              {/* Депозиты */}
              <span className={`font-semibold w-8 text-right ${depositsColor}`}>
                {o.deposits}
              </span>

              {/* Лиды */}
              <span className="text-muted w-8 text-right">
                {o.leads}
              </span>

              {/* ROAS */}
              {roas !== null && (
                <span className="text-muted ml-auto">
                  {roas}%
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* Легенда колонок */}
      <div className="flex items-center gap-2 text-[10px] text-muted mt-2 pl-4">
        <span className="w-16 text-right">Расход</span>
        <span className="w-8 text-right">Деп</span>
        <span className="w-8 text-right">Лиды</span>
        <span className="ml-auto">ROAS</span>
      </div>
    </div>
  );
}
