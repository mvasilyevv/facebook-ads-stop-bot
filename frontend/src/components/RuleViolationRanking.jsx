// Рейтинг нарушений правил
const BADGE_STYLES = [
  'bg-danger-muted text-danger',   // #1
  'bg-warning-muted text-warning', // #2
  'bg-accent-muted text-accent',   // #3+
];

export function RuleViolationRanking({ data = [] }) {
  return (
    <div>
      <h3 className="mb-3 text-2xs font-bold uppercase tracking-widest text-muted">
        Нарушения правил
      </h3>
      {!data.length && (
        <div className="py-4 text-center text-sm text-muted">Нарушений нет</div>
      )}
      <div className="divide-y divide-border">
        {data.map((item, i) => (
          <div key={item.rule ?? i} className="flex items-center gap-3 py-2">
            <span className="w-4 text-right text-[10px] font-bold text-muted">{i + 1}</span>
            <span className="flex-1 truncate text-sm text-secondary">
              {item.rule || item.rule_short || '—'}
            </span>
            <span className={`rounded-sm px-2 py-0.5 font-mono text-2xs font-bold ${BADGE_STYLES[Math.min(i, 2)]}`}>
              {item.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
