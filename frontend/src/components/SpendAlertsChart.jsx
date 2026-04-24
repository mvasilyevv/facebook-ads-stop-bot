import {
  ComposedChart, Area, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import {
  CHART_COLORS,
  CHART_SERIES_LABELS,
  ChartTooltipFrame,
  TooltipRow,
  commonAxisProps,
  monoAxisTick,
} from './charts/chartTheme.jsx';

function CombinedTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const visible = payload.filter((p) => Number(p.value ?? 0) > 0);
  if (!visible.length) return null;

  return (
    <ChartTooltipFrame label={label}>
      {visible.map((p) => {
        const isSpend = p.dataKey === 'spend';
        return (
          <TooltipRow
            key={p.dataKey}
            color={p.color || p.stroke || p.fill}
            marker={isSpend ? 'dot' : 'bar'}
            name={CHART_SERIES_LABELS[p.dataKey] || p.name}
            value={isSpend ? `$${Number(p.value).toFixed(2)}` : p.value}
          />
        );
      })}
    </ChartTooltipFrame>
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

  const chartData = labels.map((label) => ({
    label,
    spend: spendByLabel[label] ?? 0,
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

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="text-2xs font-bold uppercase tracking-widest text-muted">
            Расход и алерты — сегодня по часам
          </h3>
          <p className="mt-1 text-2xs text-secondary">
            Сопоставление расхода и моментов, где появились предупреждения или стопы.
          </p>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 8, right: 18, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="spendArea" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART_COLORS.spend} stopOpacity={0.34} />
              <stop offset="100%" stopColor={CHART_COLORS.spend} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="0" stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="label"
            {...commonAxisProps}
            interval={tickInterval}
          />
          <YAxis
            yAxisId="spend"
            orientation="left"
            tick={monoAxisTick}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v}`}
          />
          <YAxis
            yAxisId="alerts"
            orientation="right"
            tick={{ fontSize: 11, fill: CHART_COLORS.tick }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<CombinedTooltip />} />
          <Legend
            verticalAlign="bottom"
            wrapperStyle={{ fontSize: '12px', color: CHART_COLORS.tick, paddingTop: '8px' }}
            formatter={(v) => CHART_SERIES_LABELS[v] || v}
          />
          {hasSpend && (
            <Area
              yAxisId="spend"
              type="monotone"
              dataKey="spend"
              stroke={CHART_COLORS.spend}
              fill="url(#spendArea)"
              strokeWidth={2.5}
              dot={false}
              activeDot={{ r: 4, fill: CHART_COLORS.spend }}
            />
          )}
          {hasAlerts && (
            <>
              <Bar yAxisId="alerts" dataKey="warning" stackId="alerts" fill={CHART_COLORS.warning} radius={[2, 2, 0, 0]} opacity={0.9} />
              <Bar yAxisId="alerts" dataKey="stop" stackId="alerts" fill={CHART_COLORS.stop} radius={[2, 2, 0, 0]} opacity={0.95} />
            </>
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
