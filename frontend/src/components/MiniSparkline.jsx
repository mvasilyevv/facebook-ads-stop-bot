import { ResponsiveContainer, AreaChart, Area, Tooltip } from 'recharts';

/**
 * Мини-спарклайн: тонкая area-линия без осей.
 * Используется в таблице сравнения офферов.
 */
export default function MiniSparkline({ values = [], color = '#C9A227', height = 36 }) {
  if (!values || values.length === 0) {
    return <div style={{ height }} className="flex items-center justify-center text-muted text-xs">—</div>;
  }

  const data = values.map((v, i) => ({ i, v }));
  const hasData = values.some((v) => v > 0);
  if (!hasData) {
    return <div style={{ height }} className="flex items-center justify-center text-muted text-xs">—</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <defs>
          <linearGradient id={`sparkGrad-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Tooltip
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            return (
              <div className="rounded bg-elevated border border-border px-2 py-1 text-2xs text-primary shadow">
                ${payload[0].value?.toFixed(0) ?? 0}
              </div>
            );
          }}
        />
        <Area
          type="monotone"
          dataKey="v"
          stroke={color}
          strokeWidth={1.5}
          fill={`url(#sparkGrad-${color.replace('#', '')})`}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
