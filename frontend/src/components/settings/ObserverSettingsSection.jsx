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

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(0)}%`;
}

function confidenceLabel(value) {
  return { HIGH: 'высокая', MEDIUM: 'средняя', LOW: 'низкая' }[value] || '—';
}

/** Блок рекомендаций порогов по историческим данным */
function ThresholdRecommendationsPanel({
  recommendations,
  loading,
  error,
  onReload,
  onApply,
}) {
  const steps = recommendations?.steps || [];
  const applicable = steps.filter((step) => step.can_apply);

  return (
    <div className="mb-4 rounded-md border border-border bg-elevated/40 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-primary">Рекомендации по истории</h3>
          <p className="mt-1 text-2xs text-muted">
            Расчёт берёт последние {recommendations?.days ?? 14} дней и сравнивает фактическую стоимость с базовыми лимитами офферов.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-2xs"
            onClick={() => onReload?.()}
            disabled={loading}
          >
            {loading ? 'Считаем…' : 'Обновить'}
          </button>
          <button
            type="button"
            className="btn-primary px-2 py-1 text-2xs"
            onClick={() => onApply?.()}
            disabled={applicable.length === 0}
          >
            Применить все
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-danger/25 bg-danger-muted px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      {!error && loading && (
        <div className="grid gap-3 md:grid-cols-3">
          {[0, 1, 2].map((id) => (
            <div key={id} className="h-24 animate-pulse rounded-md bg-surface" />
          ))}
        </div>
      )}

      {!error && !loading && steps.length > 0 && (
        <div className="grid gap-3 md:grid-cols-3">
          {steps.map((step) => (
            <div key={step.step_id} className="rounded-md border border-border bg-surface px-3 py-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-accent-muted px-2 py-0.5 font-mono text-2xs font-bold text-accent">
                    {step.code}
                  </span>
                  <span className="text-xs font-medium text-primary">{step.title}</span>
                </div>
                <span className="text-2xs text-muted">{step.sample_count} замеров</span>
              </div>
              <div className="mb-2 grid grid-cols-2 gap-2 text-2xs">
                <div>
                  <div className="uppercase tracking-wider text-muted">Сейчас</div>
                  <div className="font-mono text-sm text-secondary">
                    {formatPercent(step.current_stop_percent)} / {formatPercent(step.current_warning_percent)}
                  </div>
                </div>
                <div>
                  <div className="uppercase tracking-wider text-muted">Рекоменд.</div>
                  <div className={`font-mono text-sm ${step.can_apply ? 'text-accent' : 'text-muted'}`}>
                    {formatPercent(step.recommended_stop_percent)} / {formatPercent(step.recommended_warning_percent)}
                  </div>
                </div>
              </div>
              <div className="mb-2 text-2xs leading-relaxed text-muted">
                {step.reason}
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] uppercase tracking-wider text-muted">
                  Уверенность: {confidenceLabel(step.confidence)}
                </span>
                <button
                  type="button"
                  className="rounded-sm bg-accent-muted px-2 py-1 text-2xs font-semibold text-accent disabled:opacity-40"
                  disabled={!step.can_apply}
                  onClick={() => onApply?.(step.step_id)}
                >
                  Применить
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {!error && !loading && steps.length === 0 && (
        <div className="rounded-md border border-border bg-surface px-3 py-4 text-center text-sm text-muted">
          Истории для расчёта пока нет
        </div>
      )}
    </div>
  );
}

/** Секция настроек Observer */
export function ObserverSettingsSection({
  observer,
  onChange,
  onSave,
  recommendations,
  recommendationsLoading,
  recommendationsError,
  onReloadRecommendations,
  onApplyRecommendations,
  saving,
}) {
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
          <span className="font-semibold text-red-400"> 10с</span> (стоп) →
          <span className="font-semibold text-amber-400"> 13с</span> (warning) →
          <span className="font-semibold text-sky-400"> 15с</span> (активный залив) →
          <span className="font-semibold text-green-400"> 30с</span> (спокойно) →
          <span className="font-semibold text-muted"> 55с</span> (нет объявлений).
          После STOP — немедленный ре-скан.
        </p>
      </div>

      {/* Пороги */}
      <div>
        <h3 className="text-sm font-semibold text-primary">Пороги отключения</h3>
        <p className="mb-3 text-2xs text-muted">
          Базовые лимиты из правил оффера. Здесь настраивается момент срабатывания.
        </p>

        <ThresholdRecommendationsPanel
          recommendations={recommendations}
          loading={recommendationsLoading}
          error={recommendationsError}
          onReload={onReloadRecommendations}
          onApply={onApplyRecommendations}
        />

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
