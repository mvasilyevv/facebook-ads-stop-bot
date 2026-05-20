// KPI-полоса истории: расход, лиды, реги, депозиты, ROAS + дельты
import { fmt$, fmtN, fmtRoas } from '../../utils/formatters.js';

function Delta({ current, previous, lowerIsBetter = false }) {
  if (current == null || previous == null || previous === 0) return null;
  const diff = current - previous;
  const pct = Math.abs((diff / previous) * 100).toFixed(0);
  if (pct === '0') return null;
  const up = diff > 0;
  const good = lowerIsBetter ? !up : up;
  const color = good ? 'text-success' : 'text-danger';
  return (
    <span className={`font-mono text-2xs font-semibold ${color}`}>
      {up ? '↑' : '↓'} {pct}%
    </span>
  );
}

function KPICard({ label, value, color, delta }) {
  return (
    <div className="kpi-cell">
      <span className="kpi-label">{label}</span>
      <span className={`kpi-value ${color}`}>{value}</span>
      {delta}
    </div>
  );
}

export function HistoryKPIStrip({ summary }) {
  if (!summary) return null;

  const roasVal = Number(summary.roas ?? 0);
  const roasColor = roasVal >= 3 ? 'text-success'
    : roasVal >= 1 ? 'text-warning'
    : roasVal > 0 ? 'text-danger'
    : 'text-muted';

  const kpis = [
    {
      label: 'Расход', value: fmt$(summary.total_spend), color: 'text-accent',
      delta: <Delta current={summary.total_spend} previous={summary.prev_spend} />,
    },
    {
      label: 'Лиды', value: fmtN(summary.total_leads), color: 'text-primary',
      delta: <Delta current={summary.total_leads} previous={summary.prev_leads} />,
    },
    {
      label: 'Реги', value: fmtN(summary.total_registrations), color: 'text-primary',
      delta: <Delta current={summary.total_registrations} previous={summary.prev_registrations} />,
    },
    {
      label: 'Депозиты',
      value: fmtN(summary.total_deposits),
      color: Number(summary.total_deposits ?? 0) > 0 ? 'text-success' : 'text-muted',
      delta: <Delta current={summary.total_deposits} previous={summary.prev_deposits} />,
    },
    { label: 'ROAS', value: fmtRoas(summary.roas), color: roasColor, delta: null },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {kpis.map((kpi) => (
        <KPICard key={kpi.label} {...kpi} />
      ))}
    </div>
  );
}
