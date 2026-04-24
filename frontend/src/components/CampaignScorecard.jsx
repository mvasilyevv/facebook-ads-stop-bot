// Scorecard распределения состояний + воронка конверсий

const STATE_COLORS = {
  NORMAL:       { bar: 'bg-success', label: 'Норма' },
  WARNING_SENT: { bar: 'bg-warning', label: 'Warning' },
  STOP_SENT:    { bar: 'bg-danger', label: 'Стоп' },
  DISABLED:     { bar: 'bg-neutral', label: 'Откл.' },
};

const FUNNEL_COLORS = ['#6366F1', '#A78BFA', '#F59E0B', '#10B981', '#EF4444'];

// Для NORMAL: больше = лучше; для остальных: меньше = лучше
const STATE_HIGHER_IS_BETTER = { NORMAL: true };

function DeltaBadge({ today, yesterday, state }) {
  if (yesterday == null) return null;
  const delta = today - yesterday;
  if (delta === 0) return null;

  const higherIsBetter = STATE_HIGHER_IS_BETTER[state] ?? false;
  const isGood = higherIsBetter ? delta > 0 : delta < 0;
  const color = isGood ? 'text-success' : 'text-danger';
  const arrow = delta > 0 ? '▲' : '▼';

  return (
    <span className={`font-mono text-[10px] ${color}`}>
      {arrow}{Math.abs(delta)}
    </span>
  );
}

/** Распределение состояний */
export function CampaignScorecard({ stats = null, statsYesterday = null, onStateClick }) {
  if (!stats) {
    return <div className="py-6 text-center text-sm text-muted">Нет данных</div>;
  }

  const total = stats.total_ads_monitored || 1;
  const normalCount = stats.total_ads_monitored - (stats.ads_in_warning + stats.ads_in_stop + stats.ads_claimed + stats.ads_disabled);

  const yesterdayNormalCount = statsYesterday
    ? statsYesterday.total_ads_monitored - (statsYesterday.ads_in_warning + statsYesterday.ads_in_stop + (statsYesterday.ads_claimed ?? 0) + statsYesterday.ads_disabled)
    : null;

  const distribution = [
    { state: 'NORMAL',       count: normalCount,           yesterday: yesterdayNormalCount },
    { state: 'WARNING_SENT', count: stats.ads_in_warning,  yesterday: statsYesterday?.ads_in_warning ?? null },
    { state: 'STOP_SENT',    count: stats.ads_in_stop,     yesterday: statsYesterday?.ads_in_stop ?? null },
    { state: 'DISABLED',     count: stats.ads_disabled,    yesterday: statsYesterday?.ads_disabled ?? null },
  ];

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Распределение
      </h3>
      <div className="space-y-2">
        {distribution.map((row) => {
          const cfg = STATE_COLORS[row.state];
          const barWidth = row.count > 0 ? Math.max(4, (row.count / total) * 100) : 0;
          return (
            <button
              key={row.state}
              className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-elevated/50"
              onClick={() => onStateClick?.(row.state)}
            >
              <span className="w-12 text-sm text-secondary">{cfg.label}</span>
              <div className="h-5 flex-1 overflow-hidden rounded-sm bg-elevated">
                {row.count > 0 && (
                  <div
                    className={`h-full ${cfg.bar} transition-all duration-300`}
                    style={{ width: `${barWidth}%` }}
                  />
                )}
              </div>
              <span className="w-6 text-right font-mono text-sm font-semibold text-primary">
                {row.count}
              </span>
              <DeltaBadge today={row.count} yesterday={row.yesterday} state={row.state} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Воронка конверсий */
export function FunnelChart({ funnel }) {
  if (!funnel?.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">Воронка конверсий</h3>
        <div className="py-4 text-center text-sm text-muted">Нет данных по воронке</div>
      </div>
    );
  }

  const maxCount = Math.max(...funnel.map((s) => s.count ?? 0), 1);

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Воронка конверсий
      </h3>
      <div className="space-y-1">
        {funnel.map((step, i) => {
          const count = step.count ?? 0;
          const barWidth = count > 0 ? Math.max((count / maxCount) * 100, 3) : 0;
          const prevCount = i > 0 ? (funnel[i - 1].count ?? 0) : null;
          const convRate = prevCount != null && prevCount > 0 ? (count / prevCount) * 100 : null;
          const barColor = FUNNEL_COLORS[i % FUNNEL_COLORS.length];

          const convColor = convRate == null ? 'text-secondary'
            : convRate >= 30 ? 'text-success'
            : convRate >= 10 ? 'text-warning'
            : convRate > 0 ? 'text-danger'
            : 'text-secondary';

          return (
            <div key={step.key ?? step.label}>
              {/* Бейдж конверсии между шагами */}
              {i > 0 && (
                <div className="flex items-center gap-2 py-0.5 pl-14">
                  <span className={`font-mono text-[10px] font-bold ${convColor}`}>
                    ↓ {convRate != null ? `${convRate.toFixed(1)}%` : '—'}
                  </span>
                </div>
              )}
              {/* Строка шага */}
              <div className="flex items-center gap-2">
                <span className="w-12 text-right text-2xs text-muted">{step.label}</span>
                <div className="h-5 flex-1 overflow-hidden rounded-sm bg-elevated">
                  {count > 0 && (
                    <div
                      className="h-full transition-all duration-300"
                      style={{ width: `${barWidth}%`, background: barColor }}
                    />
                  )}
                </div>
                <span className="w-8 text-right font-mono text-sm font-bold text-primary">{count}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
