import {
  ComposedChart, Bar, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import {
  CHART_COLORS,
  CHART_SERIES_LABELS,
  ChartTooltipFrame,
  TooltipRow,
  commonAxisProps,
  monoAxisTick,
} from './charts/chartTheme.jsx';

function fmtMoney(value) {
  return `$${Number(value).toFixed(2)}`;
}

function CombinedTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  const spendRow = payload.find((p) => p.dataKey === 'spendCumulative');
  const delta = spendRow?.payload?.spendDelta;
  const alertRows = payload.filter((p) => p.dataKey === 'warning' || p.dataKey === 'stop');

  return (
    <ChartTooltipFrame label={label}>
      {spendRow && (
        <>
          <TooltipRow
            color={spendRow.color || spendRow.stroke}
            marker="dot"
            name={CHART_SERIES_LABELS.spendCumulative}
            value={fmtMoney(spendRow.value ?? 0)}
          />
          <TooltipRow
            color={CHART_COLORS.neutral}
            marker="dot"
            name={CHART_SERIES_LABELS.spendDelta}
            value={fmtMoney(delta ?? 0)}
          />
        </>
      )}
      {alertRows.map((p) => (
        <TooltipRow
          key={p.dataKey}
          color={p.color || p.fill}
          marker="bar"
          name={CHART_SERIES_LABELS[p.dataKey] || p.name}
          value={String(p.value ?? 0)}
        />
      ))}
    </ChartTooltipFrame>
  );
}

/** Строит точки графика: прирост расхода за интервал + накопленный итог для tooltip. */
export function buildSpendAlertsChartData(spendData = [], alertsData = []) {
  const sortKeyByLabel = new Map();
  spendData.forEach((p, index) => {
    if (!p?.label) return;
    const ts = p.timestamp ? Date.parse(p.timestamp) : index;
    sortKeyByLabel.set(p.label, Number.isFinite(ts) ? ts : index);
  });
  alertsData.forEach((p, index) => {
    if (!p?.label || sortKeyByLabel.has(p.label)) return;
    sortKeyByLabel.set(p.label, spendData.length + index);
  });

  const labels = Array.from(sortKeyByLabel.keys()).sort(
    (a, b) => (sortKeyByLabel.get(a) ?? 0) - (sortKeyByLabel.get(b) ?? 0),
  );

  const spendByLabel = Object.fromEntries(
    spendData.map((p) => [p.label, Number(p.spend ?? 0)]),
  );
  const alertsByLabel = Object.fromEntries(
    alertsData.map((p) => [p.label, { warning: p.warning || 0, stop: p.stop || 0 }]),
  );

  let prevCumulative = 0;
  return labels.map((label) => {
    const spendCumulative = spendByLabel[label] ?? prevCumulative;
    const spendDelta = Math.max(0, spendCumulative - prevCumulative);
    prevCumulative = spendCumulative;
    return {
      label,
      spendDelta,
      spendCumulative,
      ...(alertsByLabel[label] ?? { warning: 0, stop: 0 }),
    };
  });
}

const PERIOD_CHART_TITLES = {
  today: 'сегодня по 30 минутам',
  '7d': 'за 7 дней',
  '30d': 'за 30 дней',
};

export function SpendAlertsChart({ spendData = [], alertsData = [], period = 'today' }) {
  if (!spendData.length && !alertsData.length) return null;

  const periodTitle = PERIOD_CHART_TITLES[period] || period;
  const chartData = buildSpendAlertsChartData(spendData, alertsData);
  const labels = chartData.map((row) => row.label);

  const hasAlerts = alertsData.some((p) => (p.warning || 0) + (p.stop || 0) > 0);
  const hasSpend = chartData.some((p) => Number(p.spendCumulative ?? 0) > 0);
  const maxSpend = Math.max(...chartData.map((p) => Number(p.spendCumulative ?? 0)), 0);

  if (!hasAlerts && !hasSpend) {
    return (
      <div>
        <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
          Расход и алерты — {periodTitle}
        </h3>
        <div className="py-8 text-center text-sm text-muted">Нет данных за период</div>
      </div>
    );
  }

  const tickInterval = labels.length <= 16 ? 0 : 3;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="text-2xs font-bold uppercase tracking-widest text-muted">
            Расход и алерты — {periodTitle}
          </h3>
          <p className="mt-1 text-2xs text-secondary">
            Линия — накопленный расход за период; в подсказке — прирост за интервал.
          </p>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 8, right: 18, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="0" stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="label"
            {...commonAxisProps}
            interval={tickInterval}
          />
          <YAxis
            yAxisId="spend"
            orientation="left"
            tick={monoAxisTick}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v}`}
            domain={[0, maxSpend > 0 ? Math.ceil(maxSpend * 1.08 * 100) / 100 : 'auto']}
          />
          <YAxis
            yAxisId="alerts"
            orientation="right"
            tick={{ fontSize: 11, fill: CHART_COLORS.tick }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<CombinedTooltip />} />
          <Legend
            verticalAlign="bottom"
            wrapperStyle={{ fontSize: '12px', color: CHART_COLORS.tick, paddingTop: '8px' }}
            formatter={(v) => CHART_SERIES_LABELS[v] || v}
          />
          {hasAlerts && (
            <>
              <Bar yAxisId="alerts" dataKey="warning" stackId="alerts" fill={CHART_COLORS.warning} radius={[2, 2, 0, 0]} opacity={0.9} />
              <Bar yAxisId="alerts" dataKey="stop" stackId="alerts" fill={CHART_COLORS.stop} radius={[2, 2, 0, 0]} opacity={0.95} />
            </>
          )}
          {hasSpend && (
            <Line
              yAxisId="spend"
              type="monotone"
              dataKey="spendCumulative"
              stroke={CHART_COLORS.spend}
              strokeWidth={3}
              dot={false}
              activeDot={{ r: 4, fill: CHART_COLORS.spend }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
