// Объединённый чарт: линия расхода + столбцы алертов (dual-axis)
import {
  ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';

// Палитра дизайн-системы
const COLORS = {
  spend: '#6366F1',
  early_signal: '#A78BFA',
  warning: '#F59E0B',
  stop: '#EF4444',
  grid: 'rgba(255,255,255,0.06)',
  axis: '#52525B',
  tick: '#94A3B8',
};

const NAMES = {
  spend: 'Расход',
  early_signal: 'Ранний сигнал',
  warning: 'Предупреждение',
  stop: 'Стоп',
};

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  if (payload.every((p) => !p.value || Number(p.value) === 0)) return null;

  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2.5 text-sm shadow-lg">
      <div className="mb-1.5 font-bold text-primary">{label}</div>
      {payload.map((p) => {
        if (!p.value || Number(p.value) === 0) return null;
        return (
          <div key={p.dataKey} className="flex items-center gap-2 text-2xs">
            <span
              className="inline-block h-2 w-2 flex-shrink-0"
              style={{
                borderRadius: p.dataKey === 'spend' ? '50%' : '2px',
                background: p.color,
              }}
            />
            <span className="text-secondary">{NAMES[p.dataKey] || p.name}:</span>
            <span className="font-mono font-semibold text-primary">
              {p.dataKey === 'spend' ? `$${Number(p.value).toFixed(2)}` : p.value}
            </span>
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
    early_signal: p.early_signal || 0, warning: p.warning || 0, stop: p.stop || 0,
  }]));

  const chartData = labels.map((label) => ({
    label,
    spend: spendByLabel[label] ?? 0,
    ...(alertsByLabel[label] ?? { early_signal: 0, warning: 0, stop: 0 }),
  }));

  const hasAlerts = alertsData.some((p) => (p.early_signal || 0) + (p.warning || 0) + (p.stop || 0) > 0);
  const hasSpend = spendData.some((p) => Number(p.spend ?? 0) > 0);

  // Если ни расхода, ни алертов — пустой чарт не рисуем
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

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Расход и алерты — сегодня по часам
      </h3>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="0" stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: COLORS.tick }} axisLine={{ stroke: COLORS.axis }} tickLine={false} interval={3} />
          <YAxis yAxisId="spend" orientation="left" tick={{ fontSize: 11, fill: COLORS.tick, fontFamily: 'JetBrains Mono, monospace' }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}`} />
          {hasAlerts && (
            <YAxis yAxisId="alerts" orientation="right" tick={{ fontSize: 11, fill: COLORS.tick }} axisLine={false} tickLine={false} allowDecimals={false} />
          )}
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ fontSize: '12px', color: '#94A3B8', paddingTop: '8px' }} formatter={(v) => NAMES[v] || v} />
          {hasAlerts && <Bar yAxisId="alerts" dataKey="early_signal" stackId="alerts" fill={COLORS.early_signal} opacity={0.8} />}
          {hasAlerts && <Bar yAxisId="alerts" dataKey="warning" stackId="alerts" fill={COLORS.warning} opacity={0.8} />}
          {hasAlerts && <Bar yAxisId="alerts" dataKey="stop" stackId="alerts" fill={COLORS.stop} radius={[2, 2, 0, 0]} opacity={0.8} />}
          <Line yAxisId="spend" type="monotone" dataKey="spend" stroke={COLORS.spend} strokeWidth={2} dot={false} activeDot={{ r: 4, fill: COLORS.spend }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
