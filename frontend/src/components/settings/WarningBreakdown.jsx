import { useEffect, useMemo, useState } from 'react';
import { getOfferRules, getOffers } from '../../api.js';
import { fmtMoney, getObserverStepThresholds, OBSERVER_STEP_CONFIGS, roundMoney } from './settingsUtils.js';

/** Таблица порогов по офферам — разворачивается по клику */
export function WarningBreakdown({ observer }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setLoading(true);
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
        setRows(results);
      })
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [open]);

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
      code: offer.code, name: offer.name, cpa,
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

  return (
    <div className="mt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-sm font-medium text-secondary hover:text-primary"
      >
        <span className="text-2xs">{open ? '▼' : '▶'}</span>
        Разбивка порогов по офферам
      </button>

      {open && (
        <div className="mt-3">
          {open && <p className="mb-2 text-2xs text-muted">Порог считается с точностью до цента.</p>}
          {loading ? (
            <div className="py-4 text-center text-sm text-muted">Загрузка офферов...</div>
          ) : computed.length === 0 ? (
            <div className="py-4 text-center text-sm text-muted">Нет активных офферов</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="th-sortable px-2 py-2 text-left">Оффер</th>
                    <th className="th-sortable px-2 py-2 text-right">CPA</th>
                    <th className="th-sortable px-2 py-2 text-right">CPC стоп</th>
                    <th className="th-sortable px-2 py-2 text-right text-warning">CPC warn</th>
                    <th className="th-sortable px-2 py-2 text-right">CPL стоп</th>
                    <th className="th-sortable px-2 py-2 text-right text-warning">CPL warn</th>
                    <th className="th-sortable px-2 py-2 text-right">CPR стоп</th>
                    <th className="th-sortable px-2 py-2 text-right text-warning">CPR warn</th>
                  </tr>
                </thead>
                <tbody>
                  {computed.map((row) => (
                    <tr key={row.code} className="tr-hover border-b border-border">
                      <td className="px-2 py-2">
                        <div className="font-mono text-2xs font-bold text-accent">{row.code}</div>
                        <div className="text-2xs text-muted">{row.name}</div>
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
        </div>
      )}
    </div>
  );
}
