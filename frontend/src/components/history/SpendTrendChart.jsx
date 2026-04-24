// График трендов: линия расхода + бары депозитов
import {
  ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import {
  CHART_COLORS,
  ChartTooltipFrame,
  TooltipRow,
  commonAxisProps,
  monoAxisTick,
} from '../charts/chartTheme.jsx';

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <ChartTooltipFrame label={label}>
      {payload.map((p) => (
        <TooltipRow
          key={p.dataKey}
          color={p.color}
          name={p.dataKey === 'spend' ? 'Расход' : 'Депозиты'}
          value={p.dataKey === 'spend' ? `$${Number(p.value).toFixed(2)}` : p.value}
          marker={p.dataKey === 'deposits' ? 'bar' : 'dot'}
        />
      ))}
    </ChartTooltipFrame>
  );
}

export default function SpendTrendChart({ data = [] }) {
  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Расход и депозиты по дням
        </h3>
        <div className="py-8 text-center text-sm text-muted">Нет данных</div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Расход и депозиты по дням
      </h3>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="0" stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="date"
            {...commonAxisProps}
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
            yAxisId="deps"
            orientation="right"
            tick={{ fontSize: 11, fill: CHART_COLORS.tick }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip />} />
          <Bar
            yAxisId="deps"
            dataKey="deposits"
            fill={CHART_COLORS.success}
            opacity={0.7}
            radius={[2, 2, 0, 0]}
          />
          <Line
            yAxisId="spend"
            type="monotone"
            dataKey="spend"
            stroke={CHART_COLORS.spend}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: CHART_COLORS.spend }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
