import React, { useState, useEffect, useMemo } from 'react';
import { getOffersCompare } from '../../api';

const fmtMoney = (v) => {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Number(v);
  if (n === 0) return '$0';
  if (n >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
};

const fmtPct = (v) => {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(1)}%`;
};

function KPICard({ label, value, sub }) {
  return (
    <div className="panel flex flex-1 min-w-[140px] flex-col gap-2xs p-sm">
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted">{label}</span>
      <span className="font-display text-lg font-semibold text-primary leading-none">{value}</span>
      {sub && <span className="text-2xs text-muted">{sub}</span>}
    </div>
  );
}

/**
 * Стрип ключевых метрик за 7 дней: расход, средние CPL/CPD, лидеры по spend и CR.
 * Помогает быстро схватить, что происходит, до погружения в отдельные виджеты.
 */
export default function AnalyticsKPIStrip() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const days = 7;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOffersCompare(days)
      .then((data) => {
        if (cancelled) return;
        setRows(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error(err);
        setRows([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const kpis = useMemo(() => {
    if (rows.length === 0) {
      return { spend: 0, leads: 0, deps: 0, cpl: null, cpd: null, topSpend: null, topCr: null };
    }
    let spend = 0;
    let leads = 0;
    let deps = 0;
    for (const r of rows) {
      spend += Number(r.spend_total) || 0;
      leads += Number(r.leads) || 0;
      deps += Number(r.deps) || 0;
    }
    const cpl = leads > 0 ? spend / leads : null;
    const cpd = deps > 0 ? spend / deps : null;
    const topSpend = [...rows].sort((a, b) => Number(b.spend_total) - Number(a.spend_total))[0];
    const topCr = [...rows]
      .filter((r) => Number(r.deps) > 0)
      .sort((a, b) => Number(b.cr_pct) - Number(a.cr_pct))[0];
    return { spend, leads, deps, cpl, cpd, topSpend, topCr };
  }, [rows]);

  if (loading) {
    return (
      <div className="panel p-sm text-2xs text-muted">Загрузка ключевых метрик…</div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="panel p-sm text-2xs text-muted">
        За последние {days} дней нет данных по офферам
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-sm">
      <KPICard
        label={`Расход · ${days}д`}
        value={fmtMoney(kpis.spend)}
        sub={`${kpis.leads.toLocaleString('ru-RU')} лидов · ${kpis.deps.toLocaleString('ru-RU')} депов`}
      />
      <KPICard label="Средний CPL" value={fmtMoney(kpis.cpl)} sub="расход / лиды" />
      <KPICard label="Средний CPD" value={fmtMoney(kpis.cpd)} sub="расход / депы" />
      <KPICard
        label="Лидер по расходу"
        value={<span className="uppercase tracking-wider">{kpis.topSpend?.code || '—'}</span>}
        sub={kpis.topSpend ? fmtMoney(kpis.topSpend.spend_total) : null}
      />
      <KPICard
        label="Лидер по CR"
        value={<span className="uppercase tracking-wider">{kpis.topCr?.code || '—'}</span>}
        sub={kpis.topCr ? fmtPct(kpis.topCr.cr_pct) : null}
      />
    </div>
  );
}
