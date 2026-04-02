import { useEffect, useState } from 'react';

import { WarningBreakdown } from './WarningBreakdown.jsx';
import {
  clampStepValue,
  getObserverStepThresholds,
  OBSERVER_STEP_CONFIGS,
} from './settingsUtils.js';

function normalizeIntegerDraft(value) {
  const digitsOnly = String(value ?? '').replace(/\D+/g, '');
  if (!digitsOnly) return '';
  return digitsOnly.replace(/^0+(?=\d)/, '');
}

function clampIntegerValue(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function IntegerObserverField({ id, label, value, min, max, hint, onChange }) {
  const [draft, setDraft] = useState(String(value ?? ''));

  useEffect(() => {
    setDraft(String(value ?? ''));
  }, [value]);

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
    <div className="form-group">
      <label className="form-label" htmlFor={id}>{label}</label>
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

/* Слайдер стопа: трек 5–100%, зоны: зелёная → янтарная → красная */
function StopSlider({ value, onChange }) {
  const stop = value;
  // warn absolute = stop * defaultWarning / 100, но здесь только визуализация позиции стопа
  const stopPct = ((stop - 5) / 95) * 100;

  const gradient = `linear-gradient(to right,
    rgba(16,185,129,0.35) 0%,
    rgba(16,185,129,0.35) ${Math.max(0, stopPct - 8)}%,
    rgba(245,158,11,0.4) ${Math.max(0, stopPct - 8)}%,
    rgba(245,158,11,0.4) ${stopPct}%,
    rgba(239,68,68,0.2) ${stopPct}%,
    rgba(239,68,68,0.2) 100%)`;

  return (
    <div className="obs-slider">
      <div className="obs-slider__label">Стоп (% от базового)</div>
      <div className="obs-slider__track-area">
        <div className="obs-slider__track-bg" style={{ background: gradient }} />
        <input
          type="range"
          className="obs-slider__range"
          min={5} max={100} step={5}
          value={stop}
          onChange={e => onChange(Number(e.target.value))}
        />
      </div>
      <div className="obs-slider__ticks">
        <span className="obs-slider__tick">5%</span>
        <span className="obs-slider__tick">25%</span>
        <span className="obs-slider__tick">50%</span>
        <span className="obs-slider__tick">75%</span>
        <span className="obs-slider__tick">100%</span>
      </div>
      <div className="obs-slider__badge obs-slider__badge--stop">
        × Стоп: {stop}%
      </div>
    </div>
  );
}

/* Слайдер предупреждения: трек 50–100% */
function WarnSlider({ value, stopValue, onChange }) {
  const warn = value;
  const warnPct = ((warn - 50) / 50) * 100;
  const warnAbsolute = Math.round(stopValue * warn / 100);

  const gradient = `linear-gradient(to right,
    rgba(16,185,129,0.35) 0%,
    rgba(16,185,129,0.35) ${warnPct}%,
    rgba(245,158,11,0.4) ${warnPct}%,
    rgba(245,158,11,0.4) 100%)`;

  return (
    <div className="obs-slider">
      <div className="obs-slider__label">Предупреждение (% от стопа)</div>
      <div className="obs-slider__track-area">
        <div className="obs-slider__track-bg" style={{ background: gradient }} />
        <input
          type="range"
          className="obs-slider__range"
          min={50} max={100} step={5}
          value={warn}
          onChange={e => onChange(Number(e.target.value))}
        />
      </div>
      <div className="obs-slider__ticks">
        <span className="obs-slider__tick">50%</span>
        <span className="obs-slider__tick">65%</span>
        <span className="obs-slider__tick">80%</span>
        <span className="obs-slider__tick">100%</span>
      </div>
      <div className="obs-slider__badge obs-slider__badge--warn">
        △ Предупр.: {warn}% → {warnAbsolute}% базы
      </div>
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

      {/* Пороги отключения со слайдерами */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ marginBottom: '14px' }}>
          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
            Пороги отключения
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Базовые лимиты берутся из правил оффера. Здесь настраивается фактический момент срабатывания.
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {stepThresholds.map((step) => (
            <div key={step.id} className="obs-step-card">
              <div className="obs-step-card__header">
                <div className="obs-step-card__code">{step.code}</div>
                <div>
                  <div className="obs-step-card__title">{step.title}</div>
                  <div className="obs-step-card__desc">{step.description}</div>
                </div>
              </div>
              <div className="obs-step-card__sliders">
                <StopSlider
                  value={step.stopPercent}
                  onChange={(v) => onChange({
                    ...observer,
                    [step.stopKey]: clampStepValue(v, 5, 100, 5),
                  })}
                />
                <WarnSlider
                  value={step.warningPercent}
                  stopValue={step.stopPercent}
                  onChange={(v) => onChange({
                    ...observer,
                    [step.warningKey]: clampStepValue(v, 50, 100, 5),
                  })}
                />
              </div>
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
