// Два мини-графика: линия расхода + stacked bar алертов
import {
  LineChart, BarChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

// Палитра дизайн-системы
const COLORS = {
  spend: '#6366F1',
  warning: '#F59E0B',
  stop: '#EF4444',
  grid: 'rgba(255,255,255,0.06)',
  axis: '#52525B',
  tick: '#94A3B8',
};

const NAMES = {
  spend: 'Расход',
  warning: 'Предупреждение',
  stop: 'Стоп',
};

function SpendTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const val = payload[0]?.value;
  if (!val || Number(val) === 0) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-sm shadow-lg">
      <div className="mb-1 font-bold text-primary">{label}</div>
      <div className="flex items-center gap-2 text-2xs">
        <span className="inline-block h-2 w-2 flex-shrink-0 rounded-full" style={{ background: COLORS.spend }} />
        <span className="text-secondary">{NAMES.spend}:</span>
        <span className="font-mono font-semibold text-primary">${Number(val).toFixed(2)}</span>
      </div>
    </div>
  );
}

function AlertsTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  if (payload.every((p) => !p.value || Number(p.value) === 0)) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-sm shadow-lg">
      <div className="mb-1 font-bold text-primary">{label}</div>
      {payload.map((p) => {
        if (!p.value || Number(p.value) === 0) return null;
        return (
          <div key={p.dataKey} className="flex items-center gap-2 text-2xs">
            <span className="inline-block h-2 w-2 flex-shrink-0 rounded-sm" style={{ background: p.color }} />
            <span className="text-secondary">{NAMES[p.dataKey] || p.name}:</span>
            <span className="font-mono font-semibold text-primary">{p.value}</span>
          </div>
        );
      })}
    </div>
  );
}

export function SpendAlertsChart({ spendData = [], alertsData = [] }) {
  if (!spendData.length && !alertsData.length) return null;

  const labels = Array.from(new Set([
    ...spendData.map((p) => p.label || ''),
    ...alertsData.map((p) => p.label || ''),
  ])).filter(Boolean).sort();

  const spendByLabel = Object.fromEntries(spendData.map((p) => [p.label, Number(p.spend ?? 0)]));
  const alertsByLabel = Object.fromEntries(alertsData.map((p) => [p.label, {
    warning: p.warning || 0, stop: p.stop || 0,
  }]));

  const spendChartData = labels.map((label) => ({ label, spend: spendByLabel[label] ?? 0 }));
  const alertsChartData = labels.map((label) => ({
    label,
    ...(alertsByLabel[label] ?? { warning: 0, stop: 0 }),
  }));

  const hasAlerts = alertsData.some((p) => (p.warning || 0) + (p.stop || 0) > 0);
  const hasSpend = spendData.some((p) => Number(p.spend ?? 0) > 0);

  if (!hasAlerts && !hasSpend) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Расход и алерты — сегодня по часам
        </h3>
        <div className="py-8 text-center text-sm text-muted">Нет данных за сегодня</div>
      </div>
    );
  }

  // Авто-интервал: при ≤12 точках показываем все метки, при большем — каждую 3-ю
  const tickInterval = labels.length <= 12 ? 0 : 3;

  const axisProps = {
    tick: { fontSize: 11, fill: COLORS.tick },
    axisLine: { stroke: COLORS.axis },
    tickLine: false,
    interval: tickInterval,
  };

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Расход и алерты — сегодня по часам
      </h3>

      {hasSpend && (
        <div className="mb-3">
          <div className="mb-1 text-2xs text-muted">Расход ($)</div>
          <ResponsiveContainer width="100%" height={130}>
            <LineChart data={spendChartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="0" stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis
                tick={{ fontSize: 11, fill: COLORS.tick, fontFamily: 'JetBrains Mono, monospace' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip content={<SpendTooltip />} />
              <Line
                type="monotone"
                dataKey="spend"
                stroke={COLORS.spend}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: COLORS.spend }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {hasAlerts && (
        <div>
          <div className="mb-1 text-2xs text-muted">Алерты</div>
          <ResponsiveContainer width="100%" height={130}>
            <BarChart data={alertsChartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="0" stroke={COLORS.grid} vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis tick={{ fontSize: 11, fill: COLORS.tick }} axisLine={false} tickLine={false} allowDecimals={false} />
              <Tooltip content={<AlertsTooltip />} />
              <Legend wrapperStyle={{ fontSize: '12px', color: '#94A3B8', paddingTop: '4px' }} formatter={(v) => NAMES[v] || v} />
              <Bar dataKey="warning" stackId="a" fill={COLORS.warning} opacity={0.8} />
              <Bar dataKey="stop" stackId="a" fill={COLORS.stop} radius={[2, 2, 0, 0]} opacity={0.8} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
