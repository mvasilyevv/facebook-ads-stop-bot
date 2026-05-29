import { useState, useEffect } from 'react';
import { getOfferRules, updateOfferRules } from '../../api.js';

const STEPS = [
  { key: 'cpc', label: 'CPC', basePctField: 'cpc_percent_stop', defaultBasePct: 2 },
  { key: 'cpl', label: 'CPL', basePctField: 'cpl_percent_stop', defaultBasePct: 10 },
  { key: 'cpr', label: 'CPR', basePctField: 'cpr_percent_stop', defaultBasePct: 20 },
];

const THRESHOLD_KEYS = STEPS.flatMap((s) => [
  `${s.key}_warning_percent_of_stop`,
  `${s.key}_stop_percent_of_base`,
]);

const DEFAULT_WARNING = 80;
const DEFAULT_STOP = 80;

function clampPct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(100, Math.max(1, Math.round(n)));
}

function fmtMoney(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toFixed(2)}`;
}

export default function OfferThresholdsTab({ offer, onSaved, onError }) {
  const cpa = Number(offer?.cpa_amount ?? offer?.cpa) || 0;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState(() =>
    Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, null])),
  );
  const [basePct, setBasePct] = useState({ cpc: 2, cpl: 10, cpr: 20 });
  const [baseRules, setBaseRules] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await getOfferRules(offer.id);
        if (!alive) return;
        setBaseRules(data || {});
        setValues(
          Object.fromEntries(
            THRESHOLD_KEYS.map((k) => {
              const raw = data?.[k];
              return [k, raw === null || raw === undefined || raw === '' ? null : Number(raw)];
            }),
          ),
        );
        setBasePct({
          cpc: Number(data?.cpc_percent_stop) || 2,
          cpl: Number(data?.cpl_percent_stop) || 10,
          cpr: Number(data?.cpr_percent_stop) || 20,
        });
      } catch (err) {
        if (alive) {
          const msg = err.message || 'Не удалось загрузить пороги';
          setError(msg);
          onError?.(msg);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [offer.id, onError]);

  const effective = {};
  for (const step of STEPS) {
    const warnKey = `${step.key}_warning_percent_of_stop`;
    const stopKey = `${step.key}_stop_percent_of_base`;
    const warnPct = values[warnKey] ?? DEFAULT_WARNING;
    const stopPct = values[stopKey] ?? DEFAULT_STOP;
    const base = cpa * (basePct[step.key] / 100);
    const stopValue = base * (stopPct / 100);
    const warnValue = stopValue * (warnPct / 100);
    effective[step.key] = { warnPct, stopPct, base, stopValue, warnValue };
  }

  const setPct = (key, raw) => {
    const v = clampPct(raw);
    setValues((prev) => ({ ...prev, [key]: v }));
  };

  const handleReset = () => {
    setValues(Object.fromEntries(THRESHOLD_KEYS.map((k) => [k, null])));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = { ...(baseRules || {}) };
      for (const k of THRESHOLD_KEYS) {
        if (values[k] === null || values[k] === undefined) {
          delete payload[k];
        } else {
          payload[k] = values[k];
        }
      }
      await updateOfferRules(offer.id, payload);
      onSaved?.();
    } catch (err) {
      const msg = err.message || 'Ошибка сохранения';
      setError(msg);
      onError?.(msg);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-3 py-8 text-sm text-muted">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        Загрузка порогов...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-2xs text-muted">
        CPA оффера: <span className="font-semibold text-primary">{fmtMoney(cpa)}</span>. По умолчанию
        warning 80%, stop 80%.
      </p>

      <div className="space-y-5">
        {STEPS.map((step) => {
          const warnKey = `${step.key}_warning_percent_of_stop`;
          const stopKey = `${step.key}_stop_percent_of_base`;
          const eff = effective[step.key];
          const warnOverridden = values[warnKey] !== null;
          const stopOverridden = values[stopKey] !== null;
          return (
            <div
              key={step.key}
              className="space-y-2 border-t border-border/40 pt-4 first:border-t-0 first:pt-0"
            >
              <div className="flex items-center justify-between">
                <span className="rounded bg-accent-muted px-2 py-0.5 font-mono text-2xs font-bold text-accent">
                  {step.label}
                </span>
                <span className="text-2xs text-muted">
                  база: {fmtMoney(eff.base)} ({basePct[step.key]}% CPA)
                </span>
              </div>

              <div>
                <div className="mb-1 flex items-baseline justify-between">
                  <label
                    className="text-2xs font-semibold uppercase tracking-wider text-secondary"
                    htmlFor={`th-${offer.id}-${stopKey}`}
                  >
                    stop % от базового{' '}
                    {!stopOverridden && (
                      <span className="font-normal normal-case text-muted">(по умолчанию)</span>
                    )}
                  </label>
                  <span className="font-mono text-xs text-primary">
                    {eff.stopPct}% →{' '}
                    <span className="font-semibold text-danger">{fmtMoney(eff.stopValue)}</span>
                  </span>
                </div>
                <input
                  id={`th-${offer.id}-${stopKey}`}
                  type="range"
                  min="1"
                  max="100"
                  step="1"
                  value={eff.stopPct}
                  onChange={(e) => setPct(stopKey, e.target.value)}
                  className="slider-range w-full"
                  style={{ '--pct': eff.stopPct, '--slider-fill': '#EF4444' }}
                />
              </div>

              <div>
                <div className="mb-1 flex items-baseline justify-between">
                  <label
                    className="text-2xs font-semibold uppercase tracking-wider text-secondary"
                    htmlFor={`th-${offer.id}-${warnKey}`}
                  >
                    warning % от стопа{' '}
                    {!warnOverridden && (
                      <span className="font-normal normal-case text-muted">(по умолчанию)</span>
                    )}
                  </label>
                  <span className="font-mono text-xs text-primary">
                    {eff.warnPct}% →{' '}
                    <span className="font-semibold text-warning">{fmtMoney(eff.warnValue)}</span>
                  </span>
                </div>
                <input
                  id={`th-${offer.id}-${warnKey}`}
                  type="range"
                  min="1"
                  max="100"
                  step="1"
                  value={eff.warnPct}
                  onChange={(e) => setPct(warnKey, e.target.value)}
                  className="slider-range w-full"
                  style={{ '--pct': eff.warnPct, '--slider-fill': '#F59E0B' }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <div className="rounded-md border border-danger/30 bg-danger-muted px-3 py-2 text-2xs text-danger">
          {error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-2">
        <button type="button" className="btn-ghost" onClick={handleReset} disabled={saving}>
          Сбросить на дефолты
        </button>
        <button type="button" className="btn-primary ml-auto" onClick={handleSave} disabled={saving}>
          {saving ? 'Сохранение...' : 'Сохранить пороги'}
        </button>
      </div>
    </div>
  );
}
