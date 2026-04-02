// График расхода и депозитов за сегодня — recharts LineChart
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      padding: '10px 14px',
      boxShadow: 'var(--shadow-md)',
      fontSize: '13px',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>{label}</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '2px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: p.color, display: 'inline-block', flexShrink: 0 }} />
          <span style={{ color: 'var(--text-secondary)' }}>{p.name}:</span>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>{p.value}</span>
        </div>
      ))}
    </div>
  );
}

export function SpendTimelineChart({ data = [] }) {
  if (!data.length) return null;

  const chartData = data.map((p) => ({
    label: p.label || new Date(p.timestamp).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }),
    spend: Number(p.spend ?? 0).toFixed(2),
    deposits: Number(p.deposits ?? 0),
    registrations: Number(p.registrations ?? 0),
  }));

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: '6px',
      padding: '16px',
      marginBottom: '16px',
      boxShadow: 'var(--shadow-sm)',
    }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.06em' }}>
        Расход и депозиты сегодня
      </h3>

      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-dim)" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={{ stroke: 'var(--border-color)' }}
            tickLine={false}
          />
          <YAxis
            yAxisId="spend"
            orientation="left"
            tick={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v}`}
          />
          <YAxis
            yAxisId="count"
            orientation="right"
            tick={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'JetBrains Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: '12px', color: 'var(--text-secondary)', paddingTop: '8px' }}
          />
          <Line
            yAxisId="spend"
            type="monotone"
            dataKey="spend"
            name="Расход $"
            stroke="#0ea5e9"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#0ea5e9' }}
          />
          <Line
            yAxisId="count"
            type="monotone"
            dataKey="deposits"
            name="Депозиты"
            stroke="#10b981"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#10b981' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
