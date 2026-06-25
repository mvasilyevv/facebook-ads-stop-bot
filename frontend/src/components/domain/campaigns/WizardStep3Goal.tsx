/**
 * Шаг 3 — Цель / Бюджет / Таргет / Атрибуция / Назначение.
 *
 * Секции:
 *   - Цель оптимизации — read-only «Зашито по SOP» (objective/optimization_goal/
 *     custom_event_type/bid_strategy/billing/text_optimizations не редактируются)
 *   - Бюджет (budget_level CBO/ABO, daily_budget_cents, bid_amount_cents=целевой CPA)
 *   - Таргет (countries+AQ, age_min/max, advantage_audience)
 *   - Атрибуция (click_through_days, view_through_days)
 *   - Назначение (destination_link, cta, start_date)
 */

import { type FC } from "react";
import { CALL_TO_ACTIONS } from "@fb/shared";
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

// Инварианты, зашитые по SOP — показываем read-only, без выбора в UI.
const SOP_LOCKED = [
  "Sales",
  "OFFSITE_CONVERSIONS",
  "Purchase",
  "Cost cap",
  "IMPRESSIONS",
  "OPT_OUT",
];

const ATTRIBUTION_DAYS_OPTIONS = [
  { value: "1", label: "1 день" },
  { value: "7", label: "7 дней" },
  { value: "28", label: "28 дней" },
];

export const WizardStep3Goal: FC<WizardStep3GoalProps> = ({ values, onChange, errors = {} }) => {
  // Бюджет и целевой CPA: показываем в долларах (cents/100), сохраняем в центах
  const budgetDollars = values.daily_budget_cents / 100;
  const cpaDollars = values.bid_amount_cents / 100;

  const handleBudgetChange = (v: string) => {
    const parsed = parseFloat(v);
    if (!isNaN(parsed) && parsed >= 0) {
      onChange({ daily_budget_cents: Math.round(parsed * 100) });
    }
  };

  const handleCpaChange = (v: string) => {
    const parsed = parseFloat(v);
    if (!isNaN(parsed) && parsed >= 0) {
      onChange({ bid_amount_cents: Math.round(parsed * 100) });
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

      {/* Цель оптимизации — зашита по SOP, read-only (без выбора) */}
      <section>
        <SectionLabel>ЦЕЛЬ ОПТИМИЗАЦИИ · ЗАШИТО ПО SOP</SectionLabel>
        <div className="flex flex-wrap gap-2">
          {SOP_LOCKED.map((item) => (
            <span
              key={item}
              className="inline-flex h-7 items-center rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 font-display text-[12px] text-bg-9"
            >
              {item}
            </span>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-bg-7">
          Эти параметры одинаковы для всех кампаний кабинета и не редактируются.
        </p>
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
            step={0.01}
            value={String(budgetDollars)}
            onChange={(e) => handleBudgetChange(e.target.value)}
            errorMessage={errors.daily_budget_cents}
            helpText="Hard cap: $100 000 / день"
          />
          {/* Целевой CPA = bid_amount для COST_CAP (обязателен) */}
          <Input
            label="Целевой CPA, $"
            type="number"
            min={0}
            step={0.01}
            value={String(cpaDollars)}
            onChange={(e) => handleCpaChange(e.target.value)}
            errorMessage={errors.bid_amount_cents}
            helpText="Cost cap — цена за результат"
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
        <div className="mt-4">
          {/* url_tags и text_optimizations (OPT_OUT) вычисляются/зашиты бэком по SOP — редактирование убрано */}
          <div className="flex flex-col gap-1">
            <div className="text-[11px] font-display tracking-wider uppercase text-bg-9">
              URL Tags
            </div>
            <div className="text-[12px] text-bg-7 italic">Трекинг по SOP — бэк вычисляет автоматически</div>
          </div>
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
  // Целевой CPA обязателен — COST_CAP без bid_amount бэк отклонит (money-инвариант)
  if (values.bid_amount_cents <= 0) errors.bid_amount_cents = "Укажите целевой CPA";
  if (values.countries.length === 0) errors.countries = "Укажите хотя бы одну страну";
  if (!values.start_date) errors.start_date = "Укажите дату старта";

  return errors;
}
