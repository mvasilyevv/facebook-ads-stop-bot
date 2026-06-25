/**
 * Шаг 3 — Цель / Бюджет / Таргет / Атрибуция / Назначение.
 *
 * Секции:
 *   - Цель оптимизации (objective / optimization_goal / custom_event_type)
 *   - Бюджет (budget_level CBO/ABO, daily_budget_cents, bid_strategy)
 *   - Таргет (countries+AQ, age_min/max, advantage_audience)
 *   - Атрибуция (click_through_days, view_through_days)
 *   - Назначение (destination_link, cta, start_date, url_tags)
 */

import { type FC } from "react";
import {
  CALL_TO_ACTIONS,
  CAMPAIGN_OBJECTIVES,
  defaultOptimizationGoal,
  OPTIMIZATION_GOAL_REQUIRES_EVENT,
  OPTIMIZATION_GOALS_BY_OBJECTIVE,
  PIXEL_EVENT_TYPES,
} from "@fb/shared";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { TagListInput } from "@/components/ui/TagListInput";
import type { WizardGoal } from "@/stores/campaignWizard";

interface WizardStep3GoalProps {
  values: WizardGoal;
  onChange: (v: Partial<WizardGoal>) => void;
  errors?: Partial<Record<keyof WizardGoal, string>>;
}

const BUDGET_LEVEL_OPTIONS = [
  { value: "campaign", label: "CBO — бюджет на кампании" },
  { value: "adset", label: "ABO — бюджет на adset'ах" },
];

const BID_STRATEGY_OPTIONS = [
  { value: "LOWEST_COST_WITHOUT_CAP", label: "Lowest cost (без кепа)" },
  { value: "LOWEST_COST_WITH_BID_CAP", label: "Bid cap" },
  { value: "COST_CAP", label: "Cost cap" },
];

const TEXT_OPT_OPTIONS = [
  { value: "OPT_OUT", label: "OPT_OUT (выключено)" },
  { value: "OPT_IN", label: "OPT_IN (включено)" },
];

const ATTRIBUTION_DAYS_OPTIONS = [
  { value: "1", label: "1 день" },
  { value: "7", label: "7 дней" },
  { value: "28", label: "28 дней" },
];

export const WizardStep3Goal: FC<WizardStep3GoalProps> = ({ values, onChange, errors = {} }) => {
  // Бюджет: показываем в долларах (cents/100), сохраняем в центах
  const budgetDollars = values.daily_budget_cents / 100;

  const handleBudgetChange = (v: string) => {
    const parsed = parseFloat(v);
    if (!isNaN(parsed) && parsed >= 0) {
      onChange({ daily_budget_cents: Math.round(parsed * 100) });
    }
  };

  return (
    <div className="space-y-7">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-1">
          ШАГ 3 · ЦЕЛЬ И БЮДЖЕТ
        </div>
        <h2 className="font-display text-[20px] font-medium text-bg-11 leading-tight m-0">
          Параметры залива
        </h2>
        <p className="text-[13px] text-bg-9 mt-1">
          Укажите цель, бюджет, таргет, атрибуцию и трекинговую ссылку.
        </p>
      </div>

      {/* Цель */}
      <section>
        <SectionLabel>ЦЕЛЬ ОПТИМИЗАЦИИ</SectionLabel>
        <div className="grid grid-cols-3 gap-4">
          {/* objective → допустимые optimization_goal (при смене сбрасываем goal на дефолт цели) */}
          <Select
            label="Objective"
            options={CAMPAIGN_OBJECTIVES}
            value={values.objective}
            onChange={(e) =>
              onChange({
                objective: e.target.value,
                optimization_goal: defaultOptimizationGoal(e.target.value),
              })
            }
          />
          <Select
            label="Optimization Goal"
            options={OPTIMIZATION_GOALS_BY_OBJECTIVE[values.objective] ?? []}
            value={values.optimization_goal}
            onChange={(e) => onChange({ optimization_goal: e.target.value })}
          />
          {/* custom_event_type нужен только для OFFSITE_CONVERSIONS (promoted_object пикселя) */}
          {values.optimization_goal === OPTIMIZATION_GOAL_REQUIRES_EVENT ? (
            <Select
              label="Событие пикселя"
              options={PIXEL_EVENT_TYPES}
              value={values.custom_event_type}
              onChange={(e) => onChange({ custom_event_type: e.target.value })}
            />
          ) : (
            <div className="flex flex-col gap-1.5">
              <label className="text-[11px] font-display tracking-wider uppercase text-bg-9">
                Событие пикселя
              </label>
              <div className="flex h-8 items-center rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 text-[12px] text-bg-7">
                не требуется для этой оптимизации
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Бюджет */}
      <section>
        <SectionLabel>БЮДЖЕТ</SectionLabel>
        <div className="grid grid-cols-3 gap-4">
          <Select
            label="Уровень бюджета"
            options={BUDGET_LEVEL_OPTIONS}
            value={values.budget_level}
            onChange={(e) =>
              onChange({ budget_level: e.target.value as WizardGoal["budget_level"] })
            }
          />
          <Input
            label="Дневной бюджет ($)"
            type="number"
            min={1}
            max={100000}
            step={1}
            value={String(budgetDollars)}
            onChange={(e) => handleBudgetChange(e.target.value)}
            errorMessage={errors.daily_budget_cents}
            helpText="Hard cap: $100 000 / день"
          />
          <Select
            label="Bid Strategy"
            options={BID_STRATEGY_OPTIONS}
            value={values.bid_strategy}
            onChange={(e) => onChange({ bid_strategy: e.target.value })}
          />
        </div>
      </section>

      {/* Таргет */}
      <section>
        <SectionLabel>ТАРГЕТ</SectionLabel>
        <div className="space-y-4">
          <div>
            <div className="text-[11px] font-display tracking-wider uppercase text-bg-9 mb-1.5">
              Страны <span className="text-bg-7">(AQ добавляется автоматически)</span>
            </div>
            <TagListInput
              id="countries"
              aria-label="Страны"
              placeholder="US, BR, DE + Enter"
              values={values.countries}
              onChange={(v) => onChange({ countries: v.map((s) => s.toUpperCase()) })}
            />
            {errors.countries && (
              <span role="alert" className="text-[11px] text-danger font-display">
                {errors.countries}
              </span>
            )}
          </div>

          <div className="grid grid-cols-3 gap-4 items-end">
            <Input
              label="Возраст от"
              type="number"
              min={13}
              max={65}
              value={String(values.age_min)}
              onChange={(e) => onChange({ age_min: Number(e.target.value) })}
            />
            <Input
              label="Возраст до"
              type="number"
              min={13}
              max={65}
              value={String(values.age_max)}
              onChange={(e) => onChange({ age_max: Number(e.target.value) })}
            />
            <div className="pb-1">
              <Switch
                checked={values.advantage_audience}
                onChange={() => onChange({ advantage_audience: !values.advantage_audience })}
                label="Advantage+ Audience"
                visualLabel="Advantage+"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Атрибуция */}
      <section>
        <SectionLabel>АТРИБУЦИЯ</SectionLabel>
        <div className="grid grid-cols-2 gap-4">
          <Select
            label="Click-through (дни)"
            options={ATTRIBUTION_DAYS_OPTIONS}
            value={String(values.click_through_days)}
            onChange={(e) => onChange({ click_through_days: Number(e.target.value) })}
          />
          <Select
            label="View-through (дни)"
            options={ATTRIBUTION_DAYS_OPTIONS}
            value={String(values.view_through_days)}
            onChange={(e) => onChange({ view_through_days: Number(e.target.value) })}
          />
        </div>
      </section>

      {/* Назначение */}
      <section>
        <SectionLabel>НАЗНАЧЕНИЕ</SectionLabel>
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Дата старта"
            type="date"
            value={values.start_date}
            onChange={(e) => onChange({ start_date: e.target.value })}
            helpText="Дефолт: завтра"
            errorMessage={errors.start_date}
          />
          <Select
            label="CTA"
            options={CALL_TO_ACTIONS}
            value={values.cta}
            onChange={(e) => onChange({ cta: e.target.value })}
          />
        </div>
        <div className="mt-4">
          <Input
            label="Destination URL (трекинг-ссылка)"
            placeholder="https://tracker.example.com/click?..."
            value={values.destination_link}
            onChange={(e) => onChange({ destination_link: e.target.value })}
            errorMessage={errors.destination_link}
            helpText="Трекинг-ссылка из AdSet.pro"
          />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4">
          {/* url_tags вычисляется бэком по SOP (url_tags_of), редактирование убрано */}
          <div className="flex flex-col gap-1">
            <div className="text-[11px] font-display tracking-wider uppercase text-bg-9">
              URL Tags
            </div>
            <div className="text-[12px] text-bg-7 italic">Трекинг по SOP — бэк вычисляет автоматически</div>
          </div>
          <Select
            label="Text Optimizations"
            options={TEXT_OPT_OPTIONS}
            value={values.text_optimizations}
            onChange={(e) => onChange({ text_optimizations: e.target.value })}
          />
        </div>
        {/* Ad text */}
        <div className="mt-4">
          <Switch
            checked={values.ad_text_mode === "text"}
            onChange={() =>
              onChange({ ad_text_mode: values.ad_text_mode === "text" ? "none" : "text" })
            }
            label="Добавить текст объявления"
            visualLabel="Текст"
          />
          {values.ad_text_mode === "text" && (
            <div className="mt-3">
              <Input
                label="Primary text"
                placeholder="Текст объявления..."
                value={values.ad_text_primary}
                onChange={(e) => onChange({ ad_text_primary: e.target.value })}
              />
            </div>
          )}
        </div>
      </section>
    </div>
  );
};

// ─── Вспомогательные компоненты ───────────────────────────────────────────────

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="font-display text-[10px] tracking-[0.14em] uppercase text-bg-7 mb-3">
      {children}
    </div>
  );
}

// ─── Валидация ────────────────────────────────────────────────────────────────

export function validateGoal(values: WizardGoal): Partial<Record<keyof WizardGoal, string>> {
  // url_tags убран из WizardGoal — бэк вычисляет по SOP
  const errors: Partial<Record<keyof WizardGoal, string>> = {};

  if (!values.destination_link.trim()) errors.destination_link = "Укажите трекинг-ссылку";
  if (values.daily_budget_cents < 100) errors.daily_budget_cents = "Минимум $1";
  if (values.daily_budget_cents > 10_000_000) errors.daily_budget_cents = "Максимум $100 000/день";
  if (values.countries.length === 0) errors.countries = "Укажите хотя бы одну страну";
  if (!values.start_date) errors.start_date = "Укажите дату старта";

  return errors;
}
