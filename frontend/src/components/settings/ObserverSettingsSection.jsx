import { useEffect, useState } from 'react';
import { WarningBreakdown } from './WarningBreakdown.jsx';
import { clampStepValue, getObserverStepThresholds, OBSERVER_STEP_CONFIGS } from './settingsUtils.js';

function normalizeIntegerDraft(value) {
  const digitsOnly = String(value ?? '').replace(/\D+/g, '');
  if (!digitsOnly) return '';
  return digitsOnly.replace(/^0+(?=\d)/, '');
}

function clampIntegerValue(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/** Инпут для целого числа с валидацией */
function IntegerObserverField({ id, label, value, min, max, hint, onChange }) {
  const [draft, setDraft] = useState(String(value ?? ''));

  useEffect(() => { setDraft(String(value ?? '')); }, [value]);

  const handleChange = (event) => {
    const nextDraft = normalizeIntegerDraft(event.target.value);
    setDraft(nextDraft);
    if (nextDraft === '') return;
    onChange(Number(nextDraft));
  };

  const handleBlur = () => {
    if (draft === '') {
      const fallback = String(clampIntegerValue(Number(value ?? min), min, max));
      setDraft(fallback);
      onChange(Number(fallback));
      return;
    }
    const normalized = String(clampIntegerValue(Number(draft), min, max));
    setDraft(normalized);
    onChange(Number(normalized));
  };

  return (
    <div>
      <label className="mb-1 block text-2xs font-semibold uppercase tracking-wider text-secondary" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="w-full rounded bg-elevated border border-border px-3 py-2 text-sm text-primary focus:border-accent focus:ring-1 focus:ring-accent focus:outline-none"
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={draft}
        onChange={handleChange}
        onBlur={handleBlur}
      />
      <div className="mt-1 text-2xs text-muted">{hint}</div>
    </div>
  );
}

/** Слайдер с визуальными зонами */
function StopSlider({ value, onChange }) {
  const stopPct = ((value - 5) / 95) * 100;

  return (
    <div className="space-y-1.5">
      <div className="text-2xs font-semibold text-secondary">Стоп (% от базового)</div>
      <div className="relative h-2 rounded-full bg-elevated overflow-hidden">
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${stopPct}%`,
            background: `linear-gradient(to right, rgba(16,185,129,0.5), rgba(245,158,11,0.5) 80%, rgba(239,68,68,0.4))`,
          }}
        />
      </div>
      <input
        type="range" min={5} max={100} step={5} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent"
      />
      <div className="badge-danger inline-block">× Стоп: {value}%</div>
    </div>
  );
}

/** Слайдер предупреждения */
function WarnSlider({ value, stopValue, onChange }) {
  const warnAbsolute = Math.round(stopValue * value / 100);

  return (
    <div className="space-y-1.5">
      <div className="text-2xs font-semibold text-secondary">Предупреждение (% от стопа)</div>
      <input
        type="range" min={50} max={100} step={5} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-warning"
      />
      <div className="badge-warning inline-block">△ Предупр.: {value}% → {warnAbsolute}% базы</div>
    </div>
  );
}

/** Секция настроек Observer */
export function ObserverSettingsSection({ observer, onChange, onSave, saving }) {
  const stepThresholds = OBSERVER_STEP_CONFIGS.map((step) => ({
    ...step,
    ...getObserverStepThresholds(observer, step),
  }));

  return (
    <section aria-label="Настройки Observer" className="panel p-5 space-y-5">
      <h2 className="text-base font-semibold text-primary">Observer — сканирование и пороги</h2>

      <div className="rounded-md border border-border bg-elevated/50 p-3 mb-4">
        <p className="text-xs text-muted">
          Интервал сканирования подстраивается автоматически по уровню угрозы:
          <span className="font-semibold text-red-400"> 15с</span> (критично) →
          <span className="font-semibold text-amber-400"> 30с</span> (повышенно) →
          <span className="font-semibold text-green-400"> 45с</span> (спокойно) →
          <span className="font-semibold text-muted"> 60с</span> (нет объявлений).
          После STOP — немедленный ре-скан.
        </p>
      </div>

      {/* Пороги */}
      <div>
        <h3 className="text-sm font-semibold text-primary">Пороги отключения</h3>
        <p className="mb-3 text-2xs text-muted">
          Базовые лимиты из правил оффера. Здесь настраивается момент срабатывания.
        </p>

        <WarningBreakdown observer={observer} />

        <div className="space-y-3">
          {stepThresholds.map((step) => (
            <div key={step.id} className="rounded-md border border-border bg-elevated/50 p-4">
              <div className="mb-3 flex items-center gap-3">
                <span className="rounded bg-accent-muted px-2 py-0.5 font-mono text-2xs font-bold text-accent">
                  {step.code}
                </span>
                <div>
                  <div className="text-sm font-medium text-primary">{step.title}</div>
                  <div className="text-2xs text-muted">{step.description}</div>
                </div>
              </div>
              <div className="space-y-4">
                <StopSlider
                  value={step.stopPercent}
                  onChange={(v) => onChange({ ...observer, [step.stopKey]: clampStepValue(v, 5, 100, 5) })}
                />
                <WarnSlider
                  value={step.warningPercent}
                  stopValue={step.stopPercent}
                  onChange={(v) => onChange({ ...observer, [step.warningKey]: clampStepValue(v, 50, 100, 5) })}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="pt-2">
        <button className="btn-primary" onClick={onSave} disabled={saving === 'observer'}>
          {saving === 'observer' ? 'Сохранение...' : 'Сохранить настройки Observer'}
        </button>
      </div>
    </section>
  );
}
