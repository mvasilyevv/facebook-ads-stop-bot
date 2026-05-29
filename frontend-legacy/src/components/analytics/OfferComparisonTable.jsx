import React, { useState, useEffect, useMemo } from 'react';
import { getOffersCompare } from '../../api';
import MiniSparkline from '../MiniSparkline.jsx';

const PERIODS = [
  { value: 7, label: '7д' },
  { value: 14, label: '14д' },
  { value: 30, label: '30д' },
];

const COLUMNS = [
  { key: 'code', label: 'Оффер', align: 'left', numeric: false },
  { key: 'spend_total', label: 'Расход', align: 'right', numeric: true },
  { key: 'leads', label: 'Лиды', align: 'right', numeric: true },
  { key: 'deps', label: 'Депы', align: 'right', numeric: true },
  { key: 'cr_pct', label: 'CR%', align: 'right', numeric: true },
  { key: 'cpl', label: 'CPL', align: 'right', numeric: true },
  { key: 'cpd', label: 'CPD', align: 'right', numeric: true },
  { key: 'trend', label: 'Динамика расхода', align: 'center', numeric: false, sortable: false },
];

const fmtMoney = (v) => {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  if (n === 0) return '$0';
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
};

const fmtInt = (v) => {
  if (v == null) return '—';
  return new Intl.NumberFormat('ru-RU').format(Number(v) || 0);
};

const fmtPct = (v) => {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(1)}%`;
};

/**
 * Таблица сравнения офферов: spend / leads / deps / CR / CPL / CPD + спарклайн расхода по дням.
 * Период переключается в шапке, сортировка по клику на заголовок.
 */
export default function OfferComparisonTable() {
  const [days, setDays] = useState(7);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState('spend_total');
  const [sortDir, setSortDir] = useState('desc');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getOffersCompare(days)
      .then((data) => {
        if (cancelled) return;
        setRows(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setError('Не удалось загрузить данные');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const enriched = useMemo(() => {
    return rows.map((r) => {
      const leads = Number(r.leads) || 0;
      const deps = Number(r.deps) || 0;
      const spend = Number(r.spend_total) || 0;
      return {
        ...r,
        spend_total: spend,
        leads,
        deps,
        cpl: leads > 0 ? spend / leads : null,
        cpd: deps > 0 ? spend / deps : null,
      };
    });
  }, [rows]);

  const sorted = useMemo(() => {
    const arr = [...enriched];
    arr.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return arr;
  }, [enriched, sortKey, sortDir]);

  /* Топ-3 для подсветки лидеров */
  const topSpendCodes = useMemo(() => {
    return [...enriched]
      .sort((a, b) => b.spend_total - a.spend_total)
      .slice(0, 3)
      .map((r) => r.code);
  }, [enriched]);
  const topCrCodes = useMemo(() => {
    return [...enriched]
      .filter((r) => r.deps > 0)
      .sort((a, b) => b.cr_pct - a.cr_pct)
      .slice(0, 3)
      .map((r) => r.code);
  }, [enriched]);

  const handleSort = (col) => {
    if (col.sortable === false) return;
    if (sortKey === col.key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(col.key);
      setSortDir(col.numeric ? 'desc' : 'asc');
    }
  };

  const sortArrow = (col) => {
    if (col.sortable === false) return null;
    if (sortKey !== col.key) return <span className="text-muted/40">↕</span>;
    return sortDir === 'asc' ? '↑' : '↓';
  };

  return (
    <div className="panel p-md">
      {/* Шапка */}
      <div className="flex flex-wrap items-center justify-between gap-sm border-b border-border pb-sm mb-md">
        <div className="flex flex-col gap-2xs">
          <span className="font-mono text-2xs uppercase tracking-wider text-text">
            Сравнение офферов
          </span>
          <span className="text-2xs text-muted">
            Суммарный расход / лиды / депозиты по офферам за выбранный период
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
      {!loading && !error && sorted.length === 0 && (
        <div className="py-lg text-center text-2xs text-muted">
          За период нет данных по офферам
        </div>
      )}

      {/* Таблица */}
      {!loading && !error && sorted.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead>
              <tr className="border-b border-border text-[10px] uppercase tracking-wider text-muted">
                {COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    onClick={() => handleSort(col)}
                    className={`whitespace-nowrap px-sm py-xs font-mono font-medium ${
                      col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'
                    } ${col.sortable === false ? '' : 'cursor-pointer select-none hover:text-primary'}`}
                  >
                    <span className="inline-flex items-center gap-2xs">
                      {col.label}
                      {sortArrow(col)}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const isTopSpend = topSpendCodes.includes(r.code);
                const isTopCr = topCrCodes.includes(r.code);
                return (
                  <tr
                    key={r.code}
                    className="border-b border-border/50 transition hover:bg-elevated/40"
                  >
                    <td className="px-sm py-sm">
                      <div className="flex items-center gap-xs">
                        <span className="font-mono font-semibold uppercase tracking-wider text-primary">{r.code}</span>
                        {isTopSpend && (
                          <span
                            className="rounded bg-accent-soft px-xs py-[2px] font-mono text-[9px] uppercase tracking-wider text-accent"
                            title="Топ-3 по расходу"
                          >
                            SPEND
                          </span>
                        )}
                        {isTopCr && (
                          <span
                            className="rounded bg-success/15 px-xs py-[2px] font-mono text-[9px] uppercase tracking-wider text-success"
                            title="Топ-3 по CR"
                          >
                            CR
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-primary">{fmtMoney(r.spend_total)}</td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-primary">{fmtInt(r.leads)}</td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-primary">{fmtInt(r.deps)}</td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-primary">{fmtPct(r.cr_pct)}</td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-secondary">{fmtMoney(r.cpl)}</td>
                    <td className="whitespace-nowrap px-sm py-sm text-right font-mono text-secondary">{fmtMoney(r.cpd)}</td>
                    <td className="w-[200px] px-sm py-sm">
                      <MiniSparkline values={r.spend_by_day || []} height={32} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
