import React, { useState, useEffect, useMemo } from 'react';
import { getAlertEvents } from '../../api';
import { RULE_LABELS_SHORT } from '../../constants/ruleLabels.js';

const PERIODS = [
  { value: 7, label: '7д' },
  { value: 14, label: '14д' },
  { value: 30, label: '30д' },
];

/**
 * Распределение причин остановок: реальные данные из /dashboard/alerts?stage=STOP.
 * Группируем по matched_rule_codes, рисуем горизонтальные бары.
 */
export default function StopReasonsDonut() {
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [events, setEvents] = useState([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    /* limit берём с запасом — на 14 дней обычно сотня-полторы алертов */
    getAlertEvents({ stage: 'STOP', limit: 200 })
      .then((data) => {
        if (cancelled) return;
        setEvents(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setError('Не удалось загрузить алерты');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reasons = useMemo(() => {
    const cutoff = Date.now() - days * 86400000;
    const counts = new Map();
    let total = 0;
    for (const ev of events) {
      const ts = ev.created_at ? new Date(ev.created_at).getTime() : 0;
      if (ts < cutoff) continue;
      const codes = ev.matched_rule_codes && ev.matched_rule_codes.length > 0
        ? ev.matched_rule_codes
        : ['unknown'];
      total += 1;
      for (const code of codes) {
        counts.set(code, (counts.get(code) || 0) + 1);
      }
    }
    const arr = Array.from(counts.entries()).map(([code, count]) => ({
      code,
      count,
      label: RULE_LABELS_SHORT[code] || code,
    }));
    arr.sort((a, b) => b.count - a.count);
    const max = arr.length > 0 ? arr[0].count : 0;
    return { items: arr, total, max };
  }, [events, days]);

  return (
    <div className="panel h-full p-md">
      {/* Шапка */}
      <div className="flex flex-wrap items-center justify-between gap-sm border-b border-border pb-sm mb-md">
        <div className="flex flex-col gap-2xs">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Причины остановок
          </span>
          <span className="text-2xs text-muted">
            Сколько раз каждое правило приводило к стопу
          </span>
        </div>
        <div className="flex items-center gap-2xs">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              onClick={() => setDays(p.value)}
              className={`rounded border px-sm py-2xs font-mono text-[10px] font-semibold transition ${
                days === p.value
                  ? 'border-accent bg-accent text-bg'
                  : 'border-border bg-elevated text-muted hover:text-primary'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Состояния */}
      {loading && (
        <div className="py-lg text-center text-2xs text-muted">Загрузка…</div>
      )}
      {error && !loading && (
        <div className="py-lg text-center text-2xs text-stop">{error}</div>
      )}
      {!loading && !error && reasons.items.length === 0 && (
        <div className="py-lg text-center text-2xs text-muted">
          За {days}д стоп-алертов нет
        </div>
      )}

      {/* Бары */}
      {!loading && !error && reasons.items.length > 0 && (
        <>
          <div className="mb-md flex items-baseline gap-xs">
            <span className="font-display text-xl font-semibold text-primary leading-none">
              {reasons.total}
            </span>
            <span className="text-2xs text-muted">всего стопов за {days}д</span>
          </div>
          <div className="space-y-sm">
            {reasons.items.map((r) => {
              const pct = reasons.max > 0 ? Math.round((r.count / reasons.max) * 100) : 0;
              const sharePct = reasons.total > 0
                ? ((r.count / reasons.total) * 100).toFixed(0)
                : 0;
              return (
                <div key={r.code} className="space-y-2xs">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-primary truncate pr-sm" title={r.label}>
                      {r.label}
                    </span>
                    <span className="whitespace-nowrap font-mono text-secondary">
                      {r.count} <span className="text-muted">· {sharePct}%</span>
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-elevated">
                    <div
                      className="h-full rounded-full bg-accent transition-all duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
