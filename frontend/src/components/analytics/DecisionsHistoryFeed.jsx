import React, { useState, useEffect, useMemo } from 'react';
import { getAlertEvents } from '../../api';
import { RULE_LABELS_SHORT } from '../../constants/ruleLabels.js';

const STAGE_FILTERS = [
  { value: 'ALL', label: 'Все' },
  { value: 'STOP', label: 'STOP' },
  { value: 'WARNING', label: 'WARNING' },
];

const stageStyle = (stage) => {
  if (stage === 'STOP') return { dot: 'bg-stop', chip: 'bg-stop/15 text-stop' };
  if (stage === 'WARNING') return { dot: 'bg-warn', chip: 'bg-warn/15 text-warn' };
  return { dot: 'bg-muted', chip: 'bg-elevated text-muted' };
};

const formatTime = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${dd}.${mm} ${hh}:${mi}`;
};

const ruleLabels = (codes) => {
  if (!codes || codes.length === 0) return '—';
  return codes.map((c) => RULE_LABELS_SHORT[c] || c).join(' · ');
};

/**
 * Лента реальных алертов из /dashboard/alerts.
 * Каждая запись — две строки: верхняя крупная (правило + оффер), нижняя серая (ad_id + время).
 */
export default function DecisionsHistoryFeed() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [stageFilter, setStageFilter] = useState('ALL');
  const [events, setEvents] = useState([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAlertEvents({ limit: 50 })
      .then((data) => {
        if (cancelled) return;
        setEvents(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setError('Не удалось загрузить ленту решений');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (stageFilter === 'ALL') return events;
    return events.filter((e) => e.stage === stageFilter);
  }, [events, stageFilter]);

  return (
    <div className="panel h-full p-md">
      {/* Шапка */}
      <div className="flex flex-wrap items-center justify-between gap-sm border-b border-border pb-sm mb-md">
        <div className="flex flex-col gap-2xs">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Последние алерты
          </span>
          <span className="text-2xs text-muted">
            Что бот заметил и какие правила сработали
          </span>
        </div>
        <div className="flex items-center gap-2xs">
          {STAGE_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStageFilter(f.value)}
              className={`rounded border px-sm py-2xs font-mono text-[10px] font-semibold transition ${
                stageFilter === f.value
                  ? 'border-accent bg-accent text-bg'
                  : 'border-border bg-elevated text-muted hover:text-primary'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Состояния */}
      {loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-muted">Загрузка…</div>
      )}
      {error && !loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-stop">{error}</div>
      )}
      {!loading && !error && filtered.length === 0 && (
        <div className="flex h-48 items-center justify-center text-2xs text-muted">
          Нет событий
        </div>
      )}

      {/* Список */}
      {!loading && !error && filtered.length > 0 && (
        <div className="max-h-[360px] space-y-xs overflow-y-auto pr-2xs">
          {filtered.map((ev) => {
            const style = stageStyle(ev.stage);
            return (
              <div
                key={ev.id}
                className="rounded border border-border/40 bg-elevated/40 px-sm py-xs transition hover:border-border hover:bg-elevated"
              >
                {/* Верхняя строка: маркер, бейдж stage, название правила */}
                <div className="flex items-center gap-xs">
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} />
                  <span className={`rounded px-xs py-[1px] font-mono text-[9px] font-semibold tracking-wider ${style.chip}`}>
                    {ev.stage}
                  </span>
                  <span className="truncate text-xs text-primary" title={ruleLabels(ev.matched_rule_codes)}>
                    {ruleLabels(ev.matched_rule_codes)}
                  </span>
                </div>

                {/* Нижняя строка: ad_name, fb_ad_id, время */}
                <div className="mt-2xs flex flex-wrap items-center gap-xs pl-[18px] text-[11px] text-muted">
                  {ev.ad_name && (
                    <span className="truncate text-secondary" title={ev.ad_name}>
                      {ev.ad_name}
                    </span>
                  )}
                  {ev.ad_name && <span>·</span>}
                  <span className="font-mono">ID {ev.fb_ad_id}</span>
                  <span>·</span>
                  <span className="font-mono">{formatTime(ev.created_at)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
