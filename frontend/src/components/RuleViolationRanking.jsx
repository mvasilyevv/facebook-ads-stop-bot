// Рейтинг нарушений правил — BarChart (горизонтальный)
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;

  const data = payload[0].payload;

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border-color)',
      borderRadius: 'var(--radius-md)',
      padding: '10px 14px',
      boxShadow: 'var(--shadow-md)',
      fontSize: '13px',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '6px' }}>
        {data.rule}
      </div>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <span style={{ color: 'var(--text-secondary)' }}>Нарушений:</span>
        <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'JetBrains Mono, monospace' }}>
          {data.count}
        </span>
      </div>
    </div>
  );
}

export function RuleViolationRanking({ data = [] }) {
  if (!data.length) return null;

  const getBarColor = (index) => {
    if (index === 0) return 'var(--accent-crimson)';
    if (index === 1) return 'var(--accent-gold)';
    return 'var(--accent-teal)';
  };

  const chartData = data.map((d, idx) => ({
    rule: String(d.rule || ''),
    rule_short: String(d.rule_short || ''),
    count: Number(d.count || 0),
    _barColor: getBarColor(idx),
  }));

  const height = Math.max(160, data.length * 36 + 40);

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
        Нарушения правил
      </div>

      <div style={{ padding: '16px' }}>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart
            layout="vertical"
            data={chartData}
            margin={{ top: 4, right: 16, left: 0, bottom: 0 }}
          >
            <XAxis type="number" hide={true} />
            <YAxis
              type="category"
              dataKey="rule_short"
              width={80}
              tick={{
                fontSize: 11,
                fill: 'var(--text-muted)',
              }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar
              dataKey="count"
              fill="var(--accent-teal)"
              radius={[0, 3, 3, 0]}
              shape={<CustomBarShape />}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function CustomBarShape(props) {
  const { fill, x, y, width, height, payload } = props;

  if (!payload || width <= 0) return null;

  return (
    <rect
      x={x}
      y={y}
      width={width}
      height={height}
      fill={payload._barColor || fill}
      rx={3}
      ry={3}
    />
  );
}
