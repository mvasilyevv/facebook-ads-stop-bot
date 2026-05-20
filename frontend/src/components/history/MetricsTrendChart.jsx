// График метрик: переключаемые линии CPL/CPR/CPC
import { useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import {
  CHART_COLORS,
  ChartTooltipFrame,
  TooltipRow,
  commonAxisProps,
  monoAxisTick,
} from '../charts/chartTheme.jsx';

const METRICS = [
  { key: 'cpl', label: 'CPL', color: CHART_COLORS.spend },
  { key: 'cpr', label: 'CPR', color: CHART_COLORS.warning },
  { key: 'cpc', label: 'CPC', color: CHART_COLORS.success },
];

function MetricTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => p.value != null && Number.isFinite(Number(p.value)));
  if (!rows.length) return null;
  return (
    <ChartTooltipFrame label={label}>
      {rows.map((p) => (
        <TooltipRow
          key={p.dataKey}
          color={p.color}
          name={p.dataKey.toUpperCase()}
          value={`$${Number(p.value).toFixed(2)}`}
        />
      ))}
    </ChartTooltipFrame>
  );
}

function MetricTabs({ active, onSelect }) {
  return (
    <div className="flex gap-1 mb-3">
      {METRICS.map((m) => (
        <button
          key={m.key}
          onClick={() => onSelect(m.key)}
          className={`px-3 py-1 rounded-md text-2xs font-medium transition-colors ${
            active === m.key
              ? 'bg-accent-muted text-accent'
              : 'text-muted hover:text-secondary hover:bg-elevated'
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

export default function MetricsTrendChart({ data = [] }) {
  const [metric, setMetric] = useState('cpl');

  if (!data.length) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Метрики по дням
        </h3>
        <div className="py-8 text-center text-sm text-muted">Нет данных</div>
      </div>
    );
  }

  const cfg = METRICS.find((m) => m.key === metric) || METRICS[0];

  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Метрики по дням
      </h3>
      <MetricTabs active={metric} onSelect={setMetric} />
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="0" stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="date"
            {...commonAxisProps}
          />
          <YAxis
            tick={monoAxisTick}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v}`}
          />
          <Tooltip content={<MetricTooltip />} />
          <Line
            type="monotone"
            dataKey={cfg.key}
            stroke={cfg.color}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            activeDot={{ r: 4, fill: cfg.color }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
