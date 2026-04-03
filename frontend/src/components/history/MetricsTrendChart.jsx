// График метрик: переключаемые линии CPL/CPR/CPC
import { useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

const METRICS = [
  { key: 'cpl', label: 'CPL', color: '#6366F1' },
  { key: 'cpr', label: 'CPR', color: '#F59E0B' },
  { key: 'cpc', label: 'CPC', color: '#22C55E' },
];

const GRID_COLOR = 'rgba(255,255,255,0.06)';
const TICK_COLOR = '#94A3B8';

function MetricTooltip({ active, payload, label }) {
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
          <span className="text-secondary">{p.dataKey.toUpperCase()}:</span>
          <span className="font-mono font-semibold text-primary">
            ${Number(p.value).toFixed(2)}
          </span>
        </div>
      ))}
    </div>
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
          <CartesianGrid strokeDasharray="0" stroke={GRID_COLOR} vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: TICK_COLOR }}
            axisLine={{ stroke: '#52525B' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: TICK_COLOR, fontFamily: 'JetBrains Mono, monospace' }}
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
            activeDot={{ r: 4, fill: cfg.color }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
