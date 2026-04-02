// Объединённый чарт: линия расхода + столбцы алертов (dual-axis)
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  if (payload.every((p) => !p.value || Number(p.value) === 0)) return null;

  const names = {
    spend: 'Расход',
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
      fontSize: '12px',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>{label}</div>
      {payload.map((p) => {
        if (!p.value || Number(p.value) === 0) return null;
        return (
          <div key={p.dataKey} style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '2px' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: p.dataKey === 'spend' ? '50%' : '2px', background: p.color, display: 'inline-block', flexShrink: 0 }} />
            <span style={{ color: 'var(--text-secondary)' }}>{names[p.dataKey] || p.name}:</span>
            <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>
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

  // Собрать все метки (часы) из обоих источников
  const labels = Array.from(new Set([
    ...spendData.map((p) => p.label || ''),
    ...alertsData.map((p) => p.label || ''),
  ])).filter(Boolean).sort();

  const spendByLabel = Object.fromEntries(
    spendData.map((p) => [p.label, Number(p.spend ?? 0)])
  );
  const alertsByLabel = Object.fromEntries(
    alertsData.map((p) => [p.label, {
      early_signal: p.early_signal || 0,
      warning: p.warning || 0,
      stop: p.stop || 0,
    }])
  );

  const chartData = labels.map((label) => ({
    label,
    spend: spendByLabel[label] ?? 0,
    ...(alertsByLabel[label] ?? { early_signal: 0, warning: 0, stop: 0 }),
  }));

  const hasAlerts = alertsData.some((p) => (p.early_signal || 0) + (p.warning || 0) + (p.stop || 0) > 0);

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
        Расход и алерты по часам
      </div>

      <div style={{ padding: '16px' }}>
        <ResponsiveContainer width="100%" height={200}>
          <ComposedChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="0" stroke="var(--border-dim)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: '#94a3b8' }}
              axisLine={{ stroke: 'var(--border-color)' }}
              tickLine={false}
              interval={3}
            />
            <YAxis
              yAxisId="spend"
              orientation="left"
              tick={{ fontSize: 11, fill: '#94a3b8', fontFamily: 'JetBrains Mono, monospace' }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => `$${v}`}
            />
            {hasAlerts && (
              <YAxis
                yAxisId="alerts"
                orientation="right"
                tick={{ fontSize: 11, fill: '#94a3b8' }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
            )}
            <Tooltip content={<CustomTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: '12px', color: 'var(--text-muted)', paddingTop: '8px' }}
              formatter={(value) => ({
                spend: 'Расход $',
                early_signal: 'Ранний сигнал',
                warning: 'Предупреждение',
                stop: 'Стоп',
              }[value] || value)}
            />
            {hasAlerts && (
              <Bar yAxisId="alerts" dataKey="early_signal" stackId="alerts" fill="#a855f7" opacity={0.8} />
            )}
            {hasAlerts && (
              <Bar yAxisId="alerts" dataKey="warning" stackId="alerts" fill="#f5a623" opacity={0.8} />
            )}
            {hasAlerts && (
              <Bar yAxisId="alerts" dataKey="stop" stackId="alerts" fill="#f74f4f" radius={[2, 2, 0, 0]} opacity={0.8} />
            )}
            <Line
              yAxisId="spend"
              type="monotone"
              dataKey="spend"
              stroke="#4f6ef7"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#4f6ef7' }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
