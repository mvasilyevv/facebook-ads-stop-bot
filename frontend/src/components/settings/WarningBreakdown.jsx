import { useEffect, useMemo, useState } from 'react';
import { getOfferRules, getOffers } from '../../api.js';
import { fmtMoney, getObserverStepThresholds, OBSERVER_STEP_CONFIGS, roundMoney } from './settingsUtils.js';

function getStepAmount(row, stepId, kind) {
  return row?.[`${stepId}${kind}`] ?? null;
}

function formatMoneyRange(values) {
  const clean = values.filter((value) => Number.isFinite(value));
  if (clean.length === 0) return '—';
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  if (min === max) return fmtMoney(min);
  return `${fmtMoney(min)}–${fmtMoney(max)}`;
}

/** Сводка порогов по офферам с подробной таблицей под раскрытием */
export function WarningBreakdown({ observer }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    getOffers()
      .then(async (offers) => {
        const active = (Array.isArray(offers) ? offers : []).filter((o) => o.is_active);
        const results = await Promise.all(
          active.map(async (offer) => {
            try {
              const rules = await getOfferRules(offer.id);
              return { offer, rules };
            } catch {
              return { offer, rules: null };
            }
          }),
        );
        if (alive) setRows(results);
      })
      .catch(() => {
        if (!alive) return;
        setRows([]);
        setError('Не удалось загрузить офферы для расчёта порогов');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
    };
  }, []);

  const thresholdsByStep = useMemo(
    () => Object.fromEntries(OBSERVER_STEP_CONFIGS.map((step) => [step.id, getObserverStepThresholds(observer, step)])),
    [observer],
  );

  const computed = useMemo(() => rows.map(({ offer, rules }) => {
    const cpa = Number(offer.cpa_amount) || 0;
    const cpcPct = Number(rules?.cpc_percent_stop) || 2;
    const cplPct = Number(rules?.cpl_percent_stop) || 10;
    const cprPct = Number(rules?.cpr_percent_stop) || 20;
    const cpcT = thresholdsByStep.cpc;
    const cplT = thresholdsByStep.cpl;
    const cprT = thresholdsByStep.cpr;
    const cpcBase = roundMoney((cpa * cpcPct) / 100);
    const cplBase = roundMoney((cpa * cplPct) / 100);
    const cprBase = roundMoney((cpa * cprPct) / 100);
    const cpcStop = roundMoney((cpcBase * cpcT.stopPercent) / 100);
    const cplStop = roundMoney((cplBase * cplT.stopPercent) / 100);
    const cprStop = roundMoney((cprBase * cprT.stopPercent) / 100);
    return {
      code: offer.code, cpa,
      cpcStop, cpcWarn: roundMoney((cpcStop * cpcT.warningPercent) / 100), cpcPct,
      cplStop, cplWarn: roundMoney((cplStop * cplT.warningPercent) / 100), cplPct,
      cprStop, cprWarn: roundMoney((cprStop * cprT.warningPercent) / 100), cprPct,
      enabled: {
        cpc: rules?.cpc_percent_enabled !== false,
        cpl: rules?.cpl_percent_enabled !== false,
        cpr: rules?.cpr_percent_enabled !== false,
      },
    };
  }), [rows, thresholdsByStep]);

  const summaries = useMemo(() => OBSERVER_STEP_CONFIGS.map((step) => {
    const activeRows = computed.filter((row) => row.enabled[step.id]);
    return {
      ...step,
      activeCount: activeRows.length,
      stopRange: formatMoneyRange(activeRows.map((row) => getStepAmount(row, step.id, 'Stop'))),
      warnRange: formatMoneyRange(activeRows.map((row) => getStepAmount(row, step.id, 'Warn'))),
    };
  }), [computed]);

  return (
    <div className="mb-4 rounded-md border border-border bg-elevated/40 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-primary">Суммы отключения по офферам</h4>
          <p className="mt-1 text-2xs text-muted">
            Значения пересчитываются сразу от текущих процентов CPC/CPL/CPR. Порог считается с точностью до цента.
          </p>
        </div>
        {!loading && !error && (
          <span className="rounded-md bg-surface px-2 py-1 text-2xs font-medium text-secondary">
            {computed.length} активн.
          </span>
        )}
      </div>

      {loading ? (
        <div className="grid gap-3 md:grid-cols-3">
          {OBSERVER_STEP_CONFIGS.map((step) => (
            <div key={step.id} className="h-24 animate-pulse rounded-md bg-surface" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-md border border-danger/25 bg-danger-muted px-3 py-2 text-sm text-danger">
          {error}
        </div>
      ) : computed.length === 0 ? (
        <div className="rounded-md border border-border bg-surface px-3 py-4 text-center text-sm text-muted">
          Нет активных офферов
        </div>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            {summaries.map((summary) => (
              <div key={summary.id} className="rounded-md border border-border bg-surface px-3 py-3">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-accent-muted px-2 py-0.5 font-mono text-2xs font-bold text-accent">
                      {summary.code}
                    </span>
                    <span className="text-xs font-medium text-primary">{summary.title}</span>
                  </div>
                  <span className="text-2xs text-muted">{summary.activeCount} правил</span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">Стоп</div>
                    <div className="mt-1 font-mono text-sm font-semibold text-danger">{summary.stopRange}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">Предупр.</div>
                    <div className="mt-1 font-mono text-sm font-semibold text-warning">{summary.warnRange}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setDetailsOpen((v) => !v)}
            className="mt-3 flex items-center gap-2 text-sm font-medium text-secondary hover:text-primary"
          >
            <span className="text-2xs">{detailsOpen ? '▼' : '▶'}</span>
            {detailsOpen ? 'Скрыть таблицу по офферам' : 'Показать таблицу по офферам'}
          </button>

          {detailsOpen && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-left">Оффер</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right">CPA</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right">CPC стоп</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right text-warning">CPC warn</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right">CPL стоп</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right text-warning">CPL warn</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right">CPR стоп</th>
                    <th className="px-2 py-2 text-2xs uppercase tracking-wider text-muted text-right text-warning">CPR warn</th>
                  </tr>
                </thead>
                <tbody>
                  {computed.map((row) => (
                    <tr key={row.code} className="tr-hover border-b border-border">
                      <td className="px-2 py-2">
                        <div className="font-mono text-2xs font-bold text-accent">{row.code}</div>
                      </td>
                      <td className="px-2 py-2 text-right font-mono font-semibold text-primary">{fmtMoney(row.cpa)}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpc ? <span className="text-danger">{fmtMoney(row.cpcStop)}</span> : <span className="text-muted">выкл</span>}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpc ? <span className="text-warning">{fmtMoney(row.cpcWarn)}</span> : '—'}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpl ? <span className="text-danger">{fmtMoney(row.cplStop)}</span> : <span className="text-muted">выкл</span>}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpl ? <span className="text-warning">{fmtMoney(row.cplWarn)}</span> : '—'}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpr ? <span className="text-danger">{fmtMoney(row.cprStop)}</span> : <span className="text-muted">выкл</span>}</td>
                      <td className="px-2 py-2 text-right font-mono">{row.enabled.cpr ? <span className="text-warning">{fmtMoney(row.cprWarn)}</span> : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
