import React, { useState, useEffect, useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { getHistoryTimeline } from '../../api';

const PERIODS = [
  { value: 7, label: '7д' },
  { value: 14, label: '14д' },
  { value: 30, label: '30д' },
];

const formatDateISO = (date) => {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
};

const formatDateShort = (iso) => {
  if (!iso) return '';
  const parts = iso.split('-');
  if (parts.length !== 3) return iso;
  return `${parts[2]}.${parts[1]}`;
};

const fmtMoney = (v) => {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  if (n === 0) return '$0';
  return `$${n.toFixed(2)}`;
};

/**
 * Динамика CPL / CPD по дням — обычный line chart Recharts с осями и тултипом.
 */
export default function CPLTimeline() {
  const [days, setDays] = useState(14);
  const [metricMode, setMetricMode] = useState('CPL');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [rawData, setRawData] = useState([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const today = new Date();
    const start = new Date();
    start.setDate(today.getDate() - (days - 1));
    getHistoryTimeline({
      date_from: formatDateISO(start),
      date_to: formatDateISO(today),
    })
      .then((data) => {
        if (cancelled) return;
        setRawData(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setError('Не удалось загрузить таймлайн');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const points = useMemo(() => {
    return rawData.map((item) => {
      const value = metricMode === 'CPL'
        ? Number(item.cpl || 0)
        : Number(item.cost_per_deposit || 0);
      return {
        date: item.date,
        label: formatDateShort(item.date),
        value: Number.isFinite(value) ? Number(value.toFixed(2)) : 0,
        spend: Number(item.spend || 0),
        leads: Number(item.leads || 0),
        deposits: Number(item.deposits || 0),
      };
    });
  }, [rawData, metricMode]);

  const summary = useMemo(() => {
    const values = points.map((p) => p.value).filter((v) => v > 0);
    if (values.length === 0) return { avg: null, last: null };
    const sum = values.reduce((a, b) => a + b, 0);
    return { avg: sum / values.length, last: points[points.length - 1]?.value ?? null };
  }, [points]);

  return (
    <div className="panel h-full p-md">
      {/* Шапка */}
      <div className="flex flex-wrap items-center justify-between gap-sm border-b border-border pb-sm mb-md">
        <div className="flex flex-col gap-2xs">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Динамика стоимости лида / депозита
          </span>
          <span className="text-2xs text-muted">
            Сколько доллар стоит лид (CPL) или депозит (CPD) по дням
          </span>
        </div>
        <div className="flex items-center gap-xs">
          <div className="flex rounded border border-border bg-elevated p-[2px]">
            {['CPL', 'CPD'].map((m) => (
              <button
                key={m}
                onClick={() => setMetricMode(m)}
                className={`rounded px-sm py-2xs font-mono text-[10px] font-semibold transition ${
                  metricMode === m ? 'bg-accent text-bg' : 'text-muted hover:text-primary'
                }`}
              >
                {m}
              </button>
            ))}
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
      </div>

      {/* Сводка */}
      {!loading && !error && points.length > 0 && (
        <div className="mb-md flex flex-wrap items-baseline gap-md text-2xs text-muted">
          <span>
            Средний {metricMode}:{' '}
            <span className="font-mono text-primary">{fmtMoney(summary.avg)}</span>
          </span>
          <span>
            Последний день:{' '}
            <span className="font-mono text-primary">{fmtMoney(summary.last)}</span>
          </span>
        </div>
      )}

      {/* График */}
      {loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-muted">Загрузка…</div>
      )}
      {error && !loading && (
        <div className="flex h-48 items-center justify-center text-2xs text-stop">{error}</div>
      )}
      {!loading && !error && points.length === 0 && (
        <div className="flex h-48 items-center justify-center text-2xs text-muted">
          За {days}д данных нет
        </div>
      )}
      {!loading && !error && points.length > 0 && (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid strokeDasharray="2 2" stroke="rgba(255,255,255,0.05)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: '#8A929D' }}
                axisLine={{ stroke: 'rgba(255,255,255,0.1)' }}
                tickLine={false}
                interval={days > 14 ? 2 : 0}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#8A929D' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                cursor={{ stroke: 'rgba(255,107,0,0.3)', strokeWidth: 1 }}
                contentStyle={{
                  background: '#15171B',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                labelStyle={{ color: '#E8EBEE', fontWeight: 600 }}
                formatter={(value, _name, ctx) => {
                  const p = ctx?.payload || {};
                  const lines = [`${metricMode}: ${fmtMoney(value)}`];
                  lines.push(`Расход: $${p.spend?.toFixed(0) ?? 0}`);
                  if (metricMode === 'CPL') lines.push(`Лиды: ${p.leads ?? 0}`);
                  else lines.push(`Депозиты: ${p.deposits ?? 0}`);
                  return [lines.join(' · '), ''];
                }}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="#FF6B00"
                strokeWidth={2}
                dot={{ r: 3, fill: '#FF6B00', stroke: '#15171B', strokeWidth: 1 }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
