import { useEffect, useMemo, useState } from 'react';
import { getOfferRules, getOffers } from '../../api.js';
import {
  fmtMoney,
  getObserverStepThresholds,
  OBSERVER_STEP_CONFIGS,
  roundMoney,
} from './settingsUtils.js';

export function WarningBreakdown({ observer }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setLoading(true);
    getOffers()
      .then(async (offers) => {
        const active = (Array.isArray(offers) ? offers : []).filter((offer) => offer.is_active);
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
    () =>
      Object.fromEntries(
        OBSERVER_STEP_CONFIGS.map((step) => [step.id, getObserverStepThresholds(observer, step)]),
      ),
    [observer],
  );

  const computed = useMemo(
    () =>
      rows.map(({ offer, rules }) => {
        const cpa = Number(offer.cpa_amount) || 0;
        const cpcPct = Number(rules?.cpc_percent_stop) || 2;
        const cplPct = Number(rules?.cpl_percent_stop) || 10;
        const cprPct = Number(rules?.cpr_percent_stop) || 20;
        const cpcThresholds = thresholdsByStep.cpc;
        const cplThresholds = thresholdsByStep.cpl;
        const cprThresholds = thresholdsByStep.cpr;
        const cpcBase = roundMoney((cpa * cpcPct) / 100);
        const cplBase = roundMoney((cpa * cplPct) / 100);
        const cprBase = roundMoney((cpa * cprPct) / 100);
        const cpcStop = roundMoney((cpcBase * cpcThresholds.stopPercent) / 100);
        const cplStop = roundMoney((cplBase * cplThresholds.stopPercent) / 100);
        const cprStop = roundMoney((cprBase * cprThresholds.stopPercent) / 100);
        return {
          code: offer.code,
          name: offer.name,
          cpa,
          cpcStop,
          cpcBase,
          cpcWarn: roundMoney((cpcStop * cpcThresholds.warningPercent) / 100),
          cpcStopPercent: cpcThresholds.stopPercent,
          cpcWarningPercent: cpcThresholds.warningPercent,
          cpcPct,
          cplStop,
          cplBase,
          cplWarn: roundMoney((cplStop * cplThresholds.warningPercent) / 100),
          cplStopPercent: cplThresholds.stopPercent,
          cplWarningPercent: cplThresholds.warningPercent,
          cplPct,
          cprStop,
          cprBase,
          cprWarn: roundMoney((cprStop * cprThresholds.warningPercent) / 100),
          cprStopPercent: cprThresholds.stopPercent,
          cprWarningPercent: cprThresholds.warningPercent,
          cprPct,
          enabled: {
            cpc: rules?.cpc_percent_enabled !== false,
            cpl: rules?.cpl_percent_enabled !== false,
            cpr: rules?.cpr_percent_enabled !== false,
          },
        };
      }),
    [rows, thresholdsByStep],
  );

  return (
    <div className="warning-breakdown">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="warning-breakdown__toggle"
      >
        <span className="warning-breakdown__toggle-icon">{open ? '▼' : '▶'}</span>
        Разбивка порогов по офферам
      </button>

      {open && (
        <div className="warning-breakdown__hint">
          Порог считается с точностью до цента, как и в observer.
        </div>
      )}

      {open && (
        <div className="warning-breakdown__table">
          {loading ? (
            <div className="warning-breakdown__state">Загрузка офферов...</div>
          ) : computed.length === 0 ? (
            <div className="warning-breakdown__state">Нет активных офферов</div>
          ) : (
            <div className="table-scroll">
              <table className="warning-breakdown__table-el">
                <thead>
                  <tr>
                    <th>Оффер</th>
                    <th>CPA</th>
                    <th>CPC стоп ({thresholdsByStep.cpc.stopPercent}%)</th>
                    <th className="warning-breakdown__accent-head">
                      CPC warn ({thresholdsByStep.cpc.warningPercent}%)
                    </th>
                    <th>CPL стоп ({thresholdsByStep.cpl.stopPercent}%)</th>
                    <th className="warning-breakdown__accent-head">
                      CPL warn ({thresholdsByStep.cpl.warningPercent}%)
                    </th>
                    <th>CPR стоп ({thresholdsByStep.cpr.stopPercent}%)</th>
                    <th className="warning-breakdown__accent-head">
                      CPR warn ({thresholdsByStep.cpr.warningPercent}%)
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {computed.map((row) => (
                    <tr key={row.code}>
                      <td>
                        <div className="warning-breakdown__offer-code">{row.code}</div>
                        <div className="warning-breakdown__offer-name">{row.name}</div>
                      </td>
                      <td className="warning-breakdown__strong">{fmtMoney(row.cpa)}</td>
                      <td>
                        {row.enabled.cpc ? (
                          <>
                            <span className="warning-breakdown__stop-value">{fmtMoney(row.cpcStop)}</span>
                            <span className="warning-breakdown__muted-inline">
                              ({row.cpcPct}% CPA)
                            </span>
                            {row.cpcStopPercent < 100 && (
                              <div className="warning-breakdown__subvalue">
                                базовый {fmtMoney(row.cpcBase)}
                              </div>
                            )}
                          </>
                        ) : (
                          <span className="warning-breakdown__muted-inline">выкл</span>
                        )}
                      </td>
                      <td>
                        {row.enabled.cpc ? (
                          <span className="warning-breakdown__warn-value">{fmtMoney(row.cpcWarn)}</span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td>
                        {row.enabled.cpl ? (
                          <>
                            <span className="warning-breakdown__stop-value">{fmtMoney(row.cplStop)}</span>
                            <span className="warning-breakdown__muted-inline">
                              ({row.cplPct}% CPA)
                            </span>
                            {row.cplStopPercent < 100 && (
                              <div className="warning-breakdown__subvalue">
                                базовый {fmtMoney(row.cplBase)}
                              </div>
                            )}
                          </>
                        ) : (
                          <span className="warning-breakdown__muted-inline">выкл</span>
                        )}
                      </td>
                      <td>
                        {row.enabled.cpl ? (
                          <span className="warning-breakdown__warn-value">{fmtMoney(row.cplWarn)}</span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td>
                        {row.enabled.cpr ? (
                          <>
                            <span className="warning-breakdown__stop-value">{fmtMoney(row.cprStop)}</span>
                            <span className="warning-breakdown__muted-inline">
                              ({row.cprPct}% CPA)
                            </span>
                            {row.cprStopPercent < 100 && (
                              <div className="warning-breakdown__subvalue">
                                базовый {fmtMoney(row.cprBase)}
                              </div>
                            )}
                          </>
                        ) : (
                          <span className="warning-breakdown__muted-inline">выкл</span>
                        )}
                      </td>
                      <td>
                        {row.enabled.cpr ? (
                          <span className="warning-breakdown__warn-value">{fmtMoney(row.cprWarn)}</span>
                        ) : (
                          '—'
                        )}
                      </td>
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
