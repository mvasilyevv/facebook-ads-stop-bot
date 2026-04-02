import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

/**
 * CampaignComparativeBars — сравнение расхода и депозитов по кампаниям.
 *
 * Props:
 * - data: массив {campaign, spend, deposits, ...}
 *
 * Вывод: топ 6 кампаний по spend, две оси Y (слева spend, справа deposits).
 */
export function CampaignComparativeBars({ data = [] }) {
  if (!data || data.length === 0) {
    return null;
  }

  // Отсортировать по spend и взять топ 6
  const topCampaigns = [...data]
    .sort((a, b) => (b.spend || 0) - (a.spend || 0))
    .slice(0, 6)
    .map((item) => ({
      ...item,
      campaign_short: (item.campaign || '')
        .substring(0, 15)
        .concat((item.campaign || '').length > 15 ? '...' : ''),
      spend_val: parseFloat(item.spend) || 0,
      deposits_val: parseInt(item.deposits, 10) || 0,
      deposits_scaled: (parseInt(item.deposits, 10) || 0) * 50, // масштабируем для видимости
    }));

  // Кастомный tooltip
  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload || payload.length === 0) return null;
    const data = payload[0].payload;
    return (
      <div
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          borderRadius: '4px',
          padding: '8px 12px',
          fontSize: '12px',
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: '4px' }}>
          {data.campaign}
        </div>
        <div style={{ color: 'var(--accent-teal)' }}>
          Расход: ${data.spend_val.toFixed(2)}
        </div>
        <div style={{ color: 'var(--accent-emerald)' }}>
          Депозиты: {data.deposits_val}
        </div>
      </div>
    );
  };

  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        borderRadius: '6px',
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      {/* Заголовок */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid var(--border-color)',
          fontSize: '13px',
          fontWeight: 700,
          textTransform: 'uppercase',
          color: 'var(--text-muted)',
          letterSpacing: '0.06em',
          backgroundColor: 'var(--bg-raised)',
        }}
      >
        Кампании: расход vs депозиты
      </div>

      {/* График */}
      <div style={{ padding: '12px 16px' }}>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={topCampaigns} margin={{ top: 16, right: 32, left: 0, bottom: 60 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-dim)" />
            <XAxis
              dataKey="campaign_short"
              angle={-45}
              textAnchor="end"
              height={80}
              tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
              interval={0}
            />
            {/* Левая ось для spend */}
            <YAxis
              yAxisId="spend"
              label={{ value: 'Spend ($)', angle: -90, position: 'insideLeft', style: { fontSize: 11 } }}
              tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            />
            {/* Правая ось для depozits */}
            <YAxis
              yAxisId="deps"
              orientation="right"
              label={{ value: 'Deposits (×50)', angle: 90, position: 'insideRight', style: { fontSize: 11 } }}
              tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar yAxisId="spend" dataKey="spend_val" fill="var(--accent-teal)" name="Расход" />
            <Bar yAxisId="deps" dataKey="deposits_scaled" fill="var(--accent-emerald)" name="Депозиты" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
