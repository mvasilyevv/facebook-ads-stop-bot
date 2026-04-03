// График трендов: линия расхода + бары депозитов
import {
  ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

const COLORS = {
  spend: '#6366F1',
  deposits: '#22C55E',
  grid: 'rgba(255,255,255,0.06)',
  axis: '#52525B',
  tick: '#94A3B8',
};

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2.5 text-sm shadow-lg">
      <div className="mb-1.5 font-bold text-primary">{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-2xs">
          <span
            className="inline-block h-2 w-2 rounded-full flex-shrink-0"
            style={{ background: p.color }}
          />
          <span className="text-secondary">
            {p.dataKey === 'spend' ? 'Расход' : 'Депозиты'}:
          </span>
          <span className="font-mono font-semibold text-primary">
            {p.dataKey === 'spend' ? `$${Number(p.value).toFixed(2)}` : p.value}
          </span>
        </div>
      ))}
    </div>
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
          <CartesianGrid strokeDasharray="0" stroke={COLORS.grid} vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: COLORS.tick }}
            axisLine={{ stroke: COLORS.axis }}
            tickLine={false}
          />
          <YAxis
            yAxisId="spend"
            orientation="left"
            tick={{ fontSize: 11, fill: COLORS.tick, fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v}`}
          />
          <YAxis
            yAxisId="deps"
            orientation="right"
            tick={{ fontSize: 11, fill: COLORS.tick }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip />} />
          <Bar
            yAxisId="deps"
            dataKey="deposits"
            fill={COLORS.deposits}
            opacity={0.7}
            radius={[2, 2, 0, 0]}
          />
          <Line
            yAxisId="spend"
            type="monotone"
            dataKey="spend"
            stroke={COLORS.spend}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: COLORS.spend }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
