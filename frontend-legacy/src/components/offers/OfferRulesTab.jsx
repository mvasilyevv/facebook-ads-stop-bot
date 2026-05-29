import { useState, useEffect } from 'react';
import { getOfferRules, updateOfferRules } from '../../api.js';
import {
  RULE_DEFS,
  DIAGNOSTIC_FIELDS,
  DEFAULT_RULES,
  inputCls,
} from './offerRulesConstants.js';

// Дни недели (0=Пн, 6=Вс)
const DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

// Пресеты временны́х весов
const PRESET_UNIFORM = () => ({
  hour_weights: Array(24).fill(1.0),
  day_weights: Array(7).fill(1.0),
});

const PRESET_NIGHT_SOFT = () => ({
  hour_weights: [
    // 0-6: ночь — мягче (порог выше → труднее стопнуть)
    1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2,
    // 7-9: утро
    1.0, 1.0, 1.0,
    // 10-18: пик — жёстче (порог ниже → легче стопнуть)
    0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9,
    // 19-23: вечер
    1.0, 1.0, 1.0, 1.0, 1.0,
  ],
  day_weights: [1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1], // выходные чуть мягче
});

function Toggle({ on, onChange, label }) {
  return (
    <button
      className="toggle-track"
      data-active={on}
      onClick={() => onChange(!on)}
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
    >
      <span className="toggle-knob" data-active={on} />
    </button>
  );
}

function RuleBlock({ rule, rules, setRules }) {
  return (
    <div className="rounded-md border border-border bg-elevated/50 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-primary">{rule.title}</div>
          {rule.hint && <p className="mt-0.5 text-2xs text-muted">{rule.hint}</p>}
        </div>
        <Toggle
          on={rules[`${rule.key}_enabled`]}
          onChange={(v) => setRules({ ...rules, [`${rule.key}_enabled`]: v })}
          label={`Включить ${rule.title}`}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {rule.fields.map((field) => (
          <div key={field.name}>
            <label
              className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
              htmlFor={`rule-${field.name}`}
            >
              {field.label}
            </label>
            <input
              id={`rule-${field.name}`}
              className={inputCls}
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={rules[field.name] || ''}
              onChange={(e) =>
                setRules({ ...rules, [field.name]: e.target.value.replace(/\D/g, '') })
              }
              disabled={!rules[`${rule.key}_enabled`]}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

// Компонент-редактор временны́х весов
function TimeWeightsEditor({ rules, setRules }) {
  const enabled = Boolean(rules.time_weights_enabled);
  // Гарантируем наличие массивов с правильной длиной
  const hourWeights = Array.isArray(rules.hour_weights) && rules.hour_weights.length === 24
    ? rules.hour_weights.map(Number)
    : Array(24).fill(1.0);
  const dayWeights = Array.isArray(rules.day_weights) && rules.day_weights.length === 7
    ? rules.day_weights.map(Number)
    : Array(7).fill(1.0);

  function applyPreset(preset) {
    const p = preset();
    setRules({ ...rules, hour_weights: p.hour_weights, day_weights: p.day_weights });
  }

  function setHourWeight(index, value) {
    const v = parseFloat(value);
    if (isNaN(v)) return;
    const next = [...hourWeights];
    next[index] = Math.round(Math.min(2.0, Math.max(0.5, v)) * 10) / 10;
    setRules({ ...rules, hour_weights: next });
  }

  function setDayWeight(index, value) {
    const v = parseFloat(value);
    if (isNaN(v)) return;
    const next = [...dayWeights];
    next[index] = Math.round(Math.min(2.0, Math.max(0.5, v)) * 10) / 10;
    setRules({ ...rules, day_weights: next });
  }

  // Цветовая шкала ячеек: < 1.0 зелёный (жёстче), > 1.0 синий (мягче)
  function cellColor(v) {
    if (v < 0.95) return 'bg-green-500/20 border-green-500/30 text-green-300';
    if (v > 1.05) return 'bg-blue-500/20 border-blue-500/30 text-blue-300';
    return 'bg-elevated border-border text-primary';
  }

  return (
    <div className="rounded-md border border-border bg-elevated/50 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium text-primary">Временны́е веса (time-of-day)</div>
          <p className="mt-0.5 text-2xs text-muted">
            Множитель на пороги CPC/CPL/CPR/spend.{' '}
            <span className="text-green-400">{'< 1.0 — жёстче'}</span>{' / '}
            <span className="text-blue-400">{'> 1.0 — мягче'}</span>.
            Итоговый вес = час × день.
          </p>
        </div>
        <Toggle
          on={enabled}
          onChange={(v) => setRules({ ...rules, time_weights_enabled: v })}
          label="Включить временны́е веса"
        />
      </div>

      {enabled && (
        <>
          {/* Пресеты */}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="px-3 py-1 rounded text-2xs border border-border bg-elevated text-secondary hover:text-primary hover:border-accent transition-colors"
              onClick={() => applyPreset(PRESET_UNIFORM)}
            >
              Сбросить (все 1.0)
            </button>
            <button
              type="button"
              className="px-3 py-1 rounded text-2xs border border-border bg-elevated text-secondary hover:text-primary hover:border-accent transition-colors"
              onClick={() => applyPreset(PRESET_NIGHT_SOFT)}
            >
              Ночные часы мягче
            </button>
          </div>

          {/* Веса по часам: 4 ряда × 6 часов */}
          <div>
            <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-secondary">
              Часы суток (0–23)
            </div>
            <div className="grid grid-cols-6 gap-1">
              {hourWeights.map((w, i) => (
                <div key={i} className="text-center">
                  <div className="text-2xs text-muted mb-0.5">{i}</div>
                  <input
                    type="number"
                    min="0.5"
                    max="2.0"
                    step="0.1"
                    value={w}
                    onChange={(e) => setHourWeight(i, e.target.value)}
                    className={`w-full rounded border px-1 py-1 text-xs text-center focus:outline-none focus:ring-1 focus:ring-accent ${cellColor(w)}`}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* Веса по дням недели */}
          <div>
            <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-secondary">
              Дни недели
            </div>
            <div className="grid grid-cols-7 gap-1">
              {dayWeights.map((w, i) => (
                <div key={i} className="text-center">
                  <div className="text-2xs text-muted mb-0.5">{DAY_LABELS[i]}</div>
                  <input
                    type="number"
                    min="0.5"
                    max="2.0"
                    step="0.1"
                    value={w}
                    onChange={(e) => setDayWeight(i, e.target.value)}
                    className={`w-full rounded border px-1 py-1 text-xs text-center focus:outline-none focus:ring-1 focus:ring-accent ${cellColor(w)}`}
                  />
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function OfferRulesTab({ offer, onSaved, onError }) {
  const [rules, setRules] = useState(DEFAULT_RULES);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const data = await getOfferRules(offer.id);
        if (!alive) return;
        setRules(data && typeof data === 'object' ? { ...DEFAULT_RULES, ...data } : DEFAULT_RULES);
      } catch (err) {
        if (alive) {
          onError?.(err.message || 'Не удалось загрузить правила');
          setRules(DEFAULT_RULES);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [offer.id, onError]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateOfferRules(offer.id, rules);
      onSaved?.();
    } catch (err) {
      onError?.(err.message || 'Ошибка сохранения');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-3 py-8 text-sm text-muted">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        Загрузка правил...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {RULE_DEFS.map((rule) => (
          <RuleBlock key={rule.key} rule={rule} rules={rules} setRules={setRules} />
        ))}
      </div>

      <h3 className="text-sm font-semibold text-primary">Диагностика CPM / частоты</h3>
      <div className="rounded-md border border-border bg-elevated/50 p-4">
        <p className="mb-3 text-2xs text-muted">CPM считается от медианы. Здесь только границы для частоты.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {DIAGNOSTIC_FIELDS.map((field) => (
            <div key={field.name}>
              <label
                className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary"
                htmlFor={`rule-${offer.id}-${field.name}`}
              >
                {field.label}
              </label>
              <input
                id={`rule-${offer.id}-${field.name}`}
                className={inputCls}
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={rules[field.name] || ''}
                onChange={(e) =>
                  setRules({ ...rules, [field.name]: e.target.value.replace(/\D/g, '') })
                }
              />
            </div>
          ))}
        </div>
      </div>

      <h3 className="text-sm font-semibold text-primary">Временны́е веса</h3>
      <TimeWeightsEditor rules={rules} setRules={setRules} />

      <div className="flex gap-2 pt-2">
        <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Сохранение...' : 'Сохранить правила'}
        </button>
      </div>
    </div>
  );
}
