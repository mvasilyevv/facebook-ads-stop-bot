import { BarChart, Bar, XAxis, YAxis, Cell, ResponsiveContainer, Tooltip } from 'recharts';

export function BudgetOverrunChart({ data = [] }) {
  if (!data.length) return null;

  const chartData = data.map(d => ({
    name: String(d.campaign || '').slice(0, 22),
    delta: Number(d.budget_delta_amount) || 0,
    pct: Number(d.budget_delta_percent) || 0,
    status: d.budget_status,
  }));

  const barColor = (status) => ({
    OVER: 'var(--accent-crimson)',
    ON_TARGET: 'var(--accent-teal)',
    UNDER: 'var(--accent-slate)',
  }[status] || 'var(--accent-slate)');

  const height = Math.max(160, chartData.length * 40);

  return (
    <div style={{ background: 'var(--bg-secondary)', padding: '16px', borderRadius: '4px', marginBottom: '16px' }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)' }}>
        Перекрут бюджета (сегодня)
      </h3>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={chartData} layout="vertical" margin={{ left: 0, right: 60 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={150} tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} />
          <Tooltip
            formatter={(val, _, props) => [`$${Math.abs(val).toFixed(2)} (${props.payload.pct > 0 ? '+' : ''}${Number(props.payload.pct).toFixed(1)}%)`, 'Отклонение']}
            contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', fontSize: '12px' }}
          />
          <Bar dataKey="delta" radius={[0, 3, 3, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={barColor(entry.status)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
