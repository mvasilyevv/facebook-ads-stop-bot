// График алертов по часам — BarChart (stacked)
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  if (payload.every((p) => !p.value)) return null;

  const labels = {
    early_signal: 'Ранний сигнал',
    warning: 'Предупреждение',
    stop: 'Стоп',
  };

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-accent)',
      borderRadius: '6px',
      padding: '10px 14px',
      boxShadow: 'var(--shadow-md)',
      fontSize: '13px',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '2px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: p.color, display: 'inline-block', flexShrink: 0 }} />
          <span style={{ color: 'var(--text-secondary)' }}>{labels[p.dataKey] || p.name}:</span>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>{p.value}</span>
        </div>
      ))}
    </div>
  );
}

export function AlertVolumeTrendline({ data = [] }) {
  if (!data.length) return null;

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      boxShadow: 'var(--shadow-sm)',
      marginBottom: '16px',
    }}>
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-color)',
        fontSize: '13px',
        fontWeight: 700,
        textTransform: 'uppercase',
        color: 'var(--text-muted)',
        letterSpacing: '0.06em',
      }}>
        Алерты по часам
      </div>

      <div style={{ padding: '16px' }}>
        <ResponsiveContainer width="100%" height={180}>
          <BarChart
            data={data}
            margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
          >
            <CartesianGrid
              strokeDasharray="0"
              stroke="var(--border-dim)"
              vertical={false}
              horizontal={true}
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              axisLine={{ stroke: 'var(--border-color)' }}
              tickLine={false}
              interval={3}
            />
            <YAxis hide={true} />
            <Tooltip content={<CustomTooltip />} />
            <Legend
              wrapperStyle={{
                fontSize: '12px',
                color: 'var(--text-muted)',
                paddingTop: '8px',
              }}
              formatter={(value) => {
                const labels = {
                  early_signal: 'Ранний сигнал',
                  warning: 'Предупреждение',
                  stop: 'Стоп',
                };
                return labels[value] || value;
              }}
            />
            <Bar
              dataKey="early_signal"
              stackId="alerts"
              fill="#a855f7"
            />
            <Bar
              dataKey="warning"
              stackId="alerts"
              fill="#f5a623"
            />
            <Bar
              dataKey="stop"
              stackId="alerts"
              fill="#f74f4f"
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
