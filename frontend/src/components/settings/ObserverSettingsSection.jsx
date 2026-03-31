import { useEffect, useState } from 'react';

import { WarningBreakdown } from './WarningBreakdown.jsx';
import {
  clampStepValue,
  getObserverStepThresholds,
  OBSERVER_STEP_CONFIGS,
  STOP_RANGE_MARKS,
  WARNING_RANGE_MARKS,
} from './settingsUtils.js';

function normalizeIntegerDraft(value) {
  const digitsOnly = String(value ?? '').replace(/\D+/g, '');
  if (!digitsOnly) return '';
  return digitsOnly.replace(/^0+(?=\d)/, '');
}

function clampIntegerValue(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function IntegerObserverField({
  id,
  label,
  value,
  min,
  max,
  hint,
  onChange,
}) {
  const [draft, setDraft] = useState(String(value ?? ''));

  useEffect(() => {
    setDraft(String(value ?? ''));
  }, [value]);

  const handleChange = (event) => {
    const nextDraft = normalizeIntegerDraft(event.target.value);
    setDraft(nextDraft);
    if (nextDraft === '') {
      return;
    }
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
    <div className="form-group">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="form-input"
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        value={draft}
        onChange={handleChange}
        onBlur={handleBlur}
      />
      <div className="form-hint">{hint}</div>
    </div>
  );
}

function PercentSlider({
  id,
  label,
  value,
  min,
  max,
  step,
  marks,
  hint,
  summary,
  onChange,
}) {
  const safeValue = clampStepValue(value, min, max, step);

  return (
    <div className="slider-field">
      <div className="slider-field__header">
        <label className="form-label slider-field__label" htmlFor={id}>
          {label}
        </label>
        <div className="slider-field__value">{safeValue}%</div>
      </div>
      <input
        id={id}
        className="slider-field__range"
        type="range"
        min={min}
        max={max}
        step={step}
        value={safeValue}
        onChange={(event) => onChange(clampStepValue(event.target.value, min, max, step))}
      />
      <div className="slider-field__scale" aria-hidden="true">
        {marks.map((mark) => (
          <span
            key={mark}
            className={`slider-field__mark ${mark === safeValue ? 'active' : ''}`}
          >
            {mark}%
          </span>
        ))}
      </div>
      <div className="slider-field__summary">{summary}</div>
      <div className="form-hint slider-field__hint">{hint}</div>
    </div>
  );
}

export function ObserverSettingsSection({ observer, onChange, onSave, saving }) {
  const stepThresholds = OBSERVER_STEP_CONFIGS.map((step) => ({
    ...step,
    ...getObserverStepThresholds(observer, step),
  }));

  return (
    <section aria-label="Настройки Observer" className="form-section">
      <div className="form-section-title">Observer — сканирование и пороги</div>
      <div className="form-grid form-grid--observer-basics">
        <IntegerObserverField
          id="obs-interval"
          label="Интервал обновления (сек)"
          value={observer.interval_seconds}
          min={10}
          max={600}
          hint="Как часто бот обновляет страницу Ads Manager. Рекомендуется 60-120 сек."
          onChange={(value) => onChange({ ...observer, interval_seconds: value })}
        />
        <IntegerObserverField
          id="obs-jitter"
          label="Jitter (сек)"
          value={observer.jitter_seconds}
          min={0}
          max={60}
          hint="Случайное отклонение ± сек для имитации человека."
          onChange={(value) => onChange({ ...observer, jitter_seconds: value })}
        />
      </div>
      <div className="observer-thresholds">
        <div className="observer-thresholds__header">
          <div>
            <div className="observer-thresholds__title">Пороги отключения</div>
            <div className="observer-thresholds__subtitle">
              Базовые лимиты по-прежнему берём из правил оффера, а здесь настраиваем
              фактический стоп и раннее предупреждение отдельно для каждого шага воронки.
            </div>
          </div>
          <div className="observer-thresholds__badge">CPC → CPL → CPR</div>
        </div>
        <div className="observer-thresholds__steps">
          {stepThresholds.map((step) => (
            <div
              key={step.id}
              className={`threshold-step threshold-step--${step.id}`}
            >
              <div className="threshold-step__topline">
                <div>
                  <div className="threshold-step__ordinal">{step.ordinal}</div>
                  <div className="threshold-step__title">
                    {step.title} <span>{step.code}</span>
                  </div>
                  <div className="threshold-step__description">{step.description}</div>
                </div>
                <div className="threshold-step__badge">
                  {step.stopShiftPercent > 0
                    ? `Раньше базового на ${step.stopShiftPercent}%`
                    : 'Базовый стоп'}
                </div>
              </div>

              <PercentSlider
                id={`obs-${step.id}-stop`}
                label="Фактический стоп (% от базового стопа)"
                value={step.stopPercent}
                min={5}
                max={100}
                step={5}
                marks={STOP_RANGE_MARKS}
                summary={
                  step.stopPercent === 100
                    ? `${step.code} срабатывает на базовом пороге оффера.`
                    : `${step.code} срабатывает раньше: на ${step.stopPercent}% от базового порога.`
                }
                hint="Фактический стоп может двигаться только вниз относительно базового лимита."
                onChange={(value) => onChange({ ...observer, [step.stopKey]: value })}
              />

              <PercentSlider
                id={`obs-${step.id}-warning`}
                label="Порог предупреждения (% от стопа)"
                value={step.warningPercent}
                min={50}
                max={100}
                step={5}
                marks={WARNING_RANGE_MARKS}
                summary={`Предупреждение для ${step.code} придёт на ${step.warningPercent}% от его фактического стопа.`}
                hint="Помогает увидеть риск заранее, ещё до реального авто-стопа."
                onChange={(value) => onChange({ ...observer, [step.warningKey]: value })}
              />
            </div>
          ))}
        </div>
      </div>
      <WarningBreakdown observer={observer} />
      <div className="settings-actions settings-actions--top">
        <button className="btn btn-primary" onClick={onSave} disabled={saving === 'observer'}>
          {saving === 'observer' ? 'Сохранение...' : 'Сохранить настройки Observer'}
        </button>
      </div>
    </section>
  );
}
