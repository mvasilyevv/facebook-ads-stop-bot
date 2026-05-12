import React, { useEffect, useState } from "react";
import { getOfferRules, updateOfferRules } from "../api.js";
import { haptic } from "../theme.js";

const STEPS = [
  { key: "cpc", label: "CPC", defaultBasePct: 2 },
  { key: "cpl", label: "CPL", defaultBasePct: 10 },
  { key: "cpr", label: "CPR", defaultBasePct: 20 },
];

const KEYS = STEPS.flatMap((s) => [
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
  if (!Number.isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

// Модалка настройки warning/stop порогов CPC/CPL/CPR для одного оффера в TMA.
// Слайдеры + визуализация цены в $ на основе CPA оффера.
export default function ThresholdsModal({ offer, onClose, onSaved, onError }) {
  const cpa = Number(offer?.cpa_amount ?? offer?.cpa) || 0;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [values, setValues] = useState(() =>
    Object.fromEntries(KEYS.map((k) => [k, null]))
  );
  const [basePct, setBasePct] = useState({ cpc: 2, cpl: 10, cpr: 20 });
  const [baseRules, setBaseRules] = useState(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const data = await getOfferRules(offer.id);
        if (!alive) return;
        setBaseRules(data || {});
        setValues(
          Object.fromEntries(
            KEYS.map((k) => {
              const raw = data?.[k];
              return [k, raw === null || raw === undefined || raw === "" ? null : Number(raw)];
            })
          )
        );
        setBasePct({
          cpc: Number(data?.cpc_percent_stop) || 2,
          cpl: Number(data?.cpl_percent_stop) || 10,
          cpr: Number(data?.cpr_percent_stop) || 20,
        });
      } catch (err) {
        if (alive) {
          onError?.(err.message || "Не удалось загрузить пороги");
          onClose();
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [offer.id]);

  // Вычисления дешёвые (3 умножения × 3 шага) — мемоизация не нужна.
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
    setValues(Object.fromEntries(KEYS.map((k) => [k, null])));
  };

  const handleSave = async () => {
    setSaving(true);
    haptic.impact("medium");
    try {
      // Шлём полный набор правил: базу + переопределения порогов (null → дефолт из схемы).
      const payload = { ...(baseRules || {}) };
      for (const k of KEYS) {
        if (values[k] === null || values[k] === undefined) {
          delete payload[k];
        } else {
          payload[k] = values[k];
        }
      }
      await updateOfferRules(offer.id, payload);
      haptic.notify("success");
      onSaved?.();
      onClose();
    } catch (err) {
      haptic.notify("error");
      onError?.(err.message || "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-sheet" onClick={(e) => e.stopPropagation()}>
        <h2>Пороги оффера {offer.code}</h2>
        <p className="hint" style={{ marginBottom: 12 }}>
          CPA оффера: <strong>{fmtMoney(cpa)}</strong>. По умолчанию warning 80%, stop 80%.
        </p>

        {loading ? (
          <p className="hint">Загрузка порогов...</p>
        ) : (
          <>
            {STEPS.map((step, idx) => {
              const warnKey = `${step.key}_warning_percent_of_stop`;
              const stopKey = `${step.key}_stop_percent_of_base`;
              const eff = effective[step.key];
              const warnOverridden = values[warnKey] !== null;
              const stopOverridden = values[stopKey] !== null;
              return (
                <div
                  key={step.key}
                  style={{
                    marginBottom: 14,
                    paddingTop: idx === 0 ? 0 : 10,
                    borderTop: idx === 0 ? "none" : "1px solid var(--tg-section-separator-color, rgba(255,255,255,0.08))",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "baseline",
                      marginBottom: 6,
                    }}
                  >
                    <span style={{ fontWeight: 700, fontSize: 14 }}>{step.label}</span>
                    <span className="hint" style={{ fontSize: 11 }}>
                      база: {fmtMoney(eff.base)} ({basePct[step.key]}% CPA)
                    </span>
                  </div>

                  {/* Stop slider */}
                  <div style={{ marginBottom: 10 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "baseline",
                        marginBottom: 4,
                      }}
                    >
                      <label className="form-label" style={{ fontSize: 12, margin: 0 }}>
                        stop % от базового{" "}
                        {!stopOverridden && <span className="hint" style={{ fontSize: 11 }}>(дефолт)</span>}
                      </label>
                      <span style={{ fontSize: 12, fontFamily: "monospace" }}>
                        {eff.stopPct}% ·{" "}
                        <strong style={{ color: "var(--color-danger, #ef4444)" }}>
                          {fmtMoney(eff.stopValue)}
                        </strong>
                      </span>
                    </div>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      step="1"
                      value={eff.stopPct}
                      onChange={(e) => setPct(stopKey, e.target.value)}
                      className="slider-range"
                      style={{ "--pct": eff.stopPct, "--slider-fill": "#EF4444" }}
                    />
                  </div>

                  {/* Warning slider */}
                  <div>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "baseline",
                        marginBottom: 4,
                      }}
                    >
                      <label className="form-label" style={{ fontSize: 12, margin: 0 }}>
                        warning % от стопа{" "}
                        {!warnOverridden && <span className="hint" style={{ fontSize: 11 }}>(дефолт)</span>}
                      </label>
                      <span style={{ fontSize: 12, fontFamily: "monospace" }}>
                        {eff.warnPct}% ·{" "}
                        <strong style={{ color: "var(--color-warning, #f59e0b)" }}>
                          {fmtMoney(eff.warnValue)}
                        </strong>
                      </span>
                    </div>
                    <input
                      type="range"
                      min="1"
                      max="100"
                      step="1"
                      value={eff.warnPct}
                      onChange={(e) => setPct(warnKey, e.target.value)}
                      className="slider-range"
                      style={{ "--pct": eff.warnPct, "--slider-fill": "#F59E0B" }}
                    />
                  </div>
                </div>
              );
            })}
          </>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Отмена
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleReset}
            disabled={loading || saving}
          >
            Сбросить
          </button>
          <button
            type="button"
            className="btn"
            onClick={handleSave}
            disabled={loading || saving}
            style={{ marginLeft: "auto" }}
          >
            {saving ? "Сохранение..." : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
