import { useState, useEffect } from 'react';
import { getOfferRules, updateOfferRules } from '../../api.js';
import {
  RULE_DEFS,
  DIAGNOSTIC_FIELDS,
  DEFAULT_RULES,
  inputCls,
} from './offerRulesConstants.js';

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

      <div className="flex gap-2 pt-2">
        <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Сохранение...' : 'Сохранить правила'}
        </button>
      </div>
    </div>
  );
}
