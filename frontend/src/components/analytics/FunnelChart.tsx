interface FunnelChartProps {
  clicks: number;
  registrations: number;
  ftds: number;
  confirmedDeposits: number;
}

const number = new Intl.NumberFormat("ru-RU");

export function FunnelChart({ clicks, registrations, ftds, confirmedDeposits }: FunnelChartProps) {
  const stages = [
    { label: "Meta clicks", value: clicks },
    { label: "Tracker registrations", value: registrations },
    { label: "FTD", value: ftds },
    { label: "Confirmed deposit", value: confirmedDeposits },
  ];
  const max = Math.max(clicks, registrations, ftds, confirmedDeposits, 1);

  return (
    <div className="flex flex-col gap-3" aria-label="Воронка Meta в Tracker">
      {stages.map((stage, index) => {
        const previous = stages[index - 1]?.value;
        const cr = previous && previous > 0 ? (stage.value / previous) * 100 : null;
        return (
          <div
            key={stage.label}
            className="grid grid-cols-[150px_minmax(0,1fr)_80px] items-center gap-3"
          >
            <span className="truncate text-[11px] text-bg-9">{stage.label}</span>
            <div className="h-5 overflow-hidden rounded-[var(--radius-1)] bg-bg-2">
              <div
                className="h-full rounded-[var(--radius-1)] bg-accent transition-[width] duration-300 motion-reduce:transition-none"
                style={{ width: `${Math.max((stage.value / max) * 100, stage.value ? 2 : 0)}%` }}
              />
            </div>
            <span className="text-right font-display text-[11px] tabular-nums text-bg-11">
              {number.format(stage.value)}
              {cr != null ? <small className="ml-1 text-bg-8">{cr.toFixed(1)}%</small> : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}
