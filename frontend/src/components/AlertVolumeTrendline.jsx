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

  const labels = {
    early_signal: 'Ранний сигнал',
    warning: 'Предупреждение',
    stop: 'Стоп',
  };

  return (
    <div style={{
      background: '#ffffff',
      border: '1px solid #e2e8f0',
      borderRadius: '6px',
      padding: '10px 14px',
      boxShadow: '0 4px 6px -1px rgba(0,0,0,0.08)',
      fontSize: '13px',
    }}>
      <div style={{ fontWeight: 700, color: '#0f172a', marginBottom: '6px' }}>{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '2px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: p.color, display: 'inline-block', flexShrink: 0 }} />
          <span style={{ color: '#475569' }}>{labels[p.dataKey] || p.name}:</span>
          <span style={{ fontWeight: 600, color: '#0f172a', fontFamily: 'JetBrains Mono, monospace' }}>{p.value}</span>
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
              stroke="#f1f5f9"
              vertical={false}
              horizontal={true}
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              axisLine={{ stroke: '#e2e8f0' }}
              tickLine={false}
              interval={3}
            />
            <YAxis hide={true} />
            <Tooltip content={<CustomTooltip />} />
            <Legend
              wrapperStyle={{
                fontSize: '12px',
                color: '#475569',
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
              fill="var(--accent-orchid)"
              radius={[2, 2, 0, 0]}
            />
            <Bar
              dataKey="warning"
              stackId="alerts"
              fill="var(--accent-gold)"
              radius={[2, 2, 0, 0]}
            />
            <Bar
              dataKey="stop"
              stackId="alerts"
              fill="var(--accent-crimson)"
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
