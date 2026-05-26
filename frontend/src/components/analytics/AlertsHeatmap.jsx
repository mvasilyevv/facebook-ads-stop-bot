import React, { useState, useEffect, useMemo } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';
import { getAlertEvents } from '../../api';

const PERIODS = [
  { value: 7, label: '7д' },
  { value: 14, label: '14д' },
  { value: 30, label: '30д' },
];

const formatDayShort = (date) => {
  const dd = String(date.getDate()).padStart(2, '0');
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}`;
};

const formatDayKey = (date) => {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};

/**
 * Активность алертов по дням: stacked-бары WARNING + STOP.
 * Заменяет недореализованный 24×7 heatmap.
 */
export default function AlertsActivityChart() {
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [events, setEvents] = useState([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    /* тянем оба stage сразу — большего лимита для общего среза достаточно */
    getAlertEvents({ limit: 200 })
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

  const data = useMemo(() => {
    /* Готовим скелет ровно из N дней, чтобы отсутствующие дни не схлопывались */
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const buckets = new Map();
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      buckets.set(formatDayKey(d), {
        day: formatDayShort(d),
        warning: 0,
        stop: 0,
      });
    }

    const cutoff = Date.now() - days * 86400000;
    for (const ev of events) {
      const ts = ev.created_at ? new Date(ev.created_at).getTime() : 0;
      if (ts < cutoff) continue;
      const d = new Date(ts);
      d.setHours(0, 0, 0, 0);
      const key = formatDayKey(d);
      const bucket = buckets.get(key);
      if (!bucket) continue;
      if (ev.stage === 'STOP') bucket.stop += 1;
      else bucket.warning += 1;
    }
    return Array.from(buckets.values());
  }, [events, days]);

  const totals = useMemo(() => {
    let warning = 0;
    let stop = 0;
    for (const d of data) {
      warning += d.warning;
      stop += d.stop;
    }
    return { warning, stop, total: warning + stop };
  }, [data]);

  return (
    <div className="panel h-full p-md">
      {/* Шапка */}
      <div className="flex flex-wrap items-center justify-between gap-sm border-b border-border pb-sm mb-md">
        <div className="flex flex-col gap-2xs">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Активность алертов по дням
          </span>
          <span className="text-2xs text-muted">
            Сколько алертов в день, с разбивкой на предупреждения и стопы
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

      {/* Итоги */}
      {!loading && !error && (
        <div className="mb-md flex flex-wrap items-baseline gap-md">
          <div className="flex items-baseline gap-xs">
            <span className="font-display text-xl font-semibold text-primary leading-none">
              {totals.total}
            </span>
            <span className="text-2xs text-muted">алертов за {days}д</span>
          </div>
          <div className="flex items-center gap-xs text-2xs">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: '#FFB020' }} />
            <span className="text-muted">WARNING: <span className="text-primary font-mono">{totals.warning}</span></span>
          </div>
          <div className="flex items-center gap-xs text-2xs">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: '#FF3B3B' }} />
            <span className="text-muted">STOP: <span className="text-primary font-mono">{totals.stop}</span></span>
          </div>
        </div>
      )}

      {/* Состояния */}
      {loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-muted">Загрузка…</div>
      )}
      {error && !loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-stop">{error}</div>
      )}

      {!loading && !error && (
        <div className="h-48 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid strokeDasharray="2 2" stroke="rgba(255,255,255,0.05)" vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10, fill: '#8A929D' }}
                axisLine={{ stroke: 'rgba(255,255,255,0.1)' }}
                tickLine={false}
                interval={days > 14 ? 2 : 0}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#8A929D' }}
                axisLine={false}
                tickLine={false}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: 'rgba(255,107,0,0.08)' }}
                contentStyle={{
                  background: '#15171B',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                labelStyle={{ color: '#E8EBEE', fontWeight: 600 }}
              />
              <Legend
                wrapperStyle={{ fontSize: 10, color: '#8A929D' }}
                iconType="square"
              />
              <Bar dataKey="warning" name="WARNING" stackId="a" fill="#FFB020" radius={[0, 0, 0, 0]} />
              <Bar dataKey="stop" name="STOP" stackId="a" fill="#FF3B3B" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
