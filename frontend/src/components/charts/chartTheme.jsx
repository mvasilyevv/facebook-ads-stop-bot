export const CHART_COLORS = {
  spend: '#818CF8',
  spendSoft: 'rgba(129, 140, 248, 0.16)',
  warning: '#F59E0B',
  stop: '#EF4444',
  success: '#10B981',
  neutral: '#64748B',
  grid: 'rgba(255,255,255,0.07)',
  axis: 'rgba(148,163,184,0.42)',
  tick: '#94A3B8',
  tooltipBg: '#151520',
};

export const CHART_SERIES_LABELS = {
  spend: 'Расход',
  warning: 'Предупреждение',
  stop: 'Стоп',
  deposits: 'Депозиты',
  cpl: 'CPL',
  cpr: 'CPR',
  cpc: 'CPC',
};

export const commonAxisProps = {
  tick: { fontSize: 11, fill: CHART_COLORS.tick },
  axisLine: { stroke: CHART_COLORS.axis },
  tickLine: false,
};

export const monoAxisTick = {
  fontSize: 11,
  fill: CHART_COLORS.tick,
  fontFamily: 'JetBrains Mono, monospace',
};

export function ChartTooltipFrame({ label, children }) {
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2.5 text-sm shadow-lg">
      {label && <div className="mb-1.5 font-semibold text-primary">{label}</div>}
      <div className="space-y-1">{children}</div>
    </div>
  );
}

export function TooltipRow({ color, name, value, marker = 'dot' }) {
  return (
    <div className="flex items-center gap-2 text-2xs">
      <span
        className={`inline-block h-2 w-2 flex-shrink-0 ${marker === 'bar' ? 'rounded-sm' : 'rounded-full'}`}
        style={{ background: color }}
      />
      <span className="text-secondary">{name}:</span>
      <span className="font-mono font-semibold text-primary">{value}</span>
    </div>
  );
}
