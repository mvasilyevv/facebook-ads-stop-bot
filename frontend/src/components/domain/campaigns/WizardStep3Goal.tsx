/**
 * Шаг 3 — Цель / Бюджет / Таргет / Атрибуция / Назначение.
 *
 * Секции:
 *   - Цель оптимизации — read-only «Зашито по SOP» (objective/optimization_goal/
 *     custom_event_type/bid_strategy/billing/text_optimizations не редактируются)
 *   - Бюджет (major-unit decimal strings in the confirmed cabinet currency)
 *   - Таргет (countries+AQ, age_min/max, advantage_audience)
 *   - Атрибуция (click_through_days, view_through_days)
 *   - Назначение (destination_link, cta, start_date)
 */

import { type FC } from "react";
import { CALL_TO_ACTIONS } from "@fb/shared";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { CountryMultiSelect } from "@/components/ui/CountryMultiSelect";
import type { WizardGoal } from "@/stores/campaignWizard";

interface WizardStep3GoalProps {
  values: WizardGoal;
  onChange: (v: Partial<WizardGoal>) => void;
  currency: string | null;
  currencyExponent: number | null;
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

export const WizardStep3Goal: FC<WizardStep3GoalProps> = ({
  values,
  onChange,
  currency,
  currencyExponent,
  errors = {},
}) => {
  const currencyLabel = currency || "валюта не подтверждена";
  const precisionLabel =
    currencyExponent == null
      ? "Точность не подтверждена"
      : currencyExponent === 0
        ? "Только целые единицы"
        : `До ${currencyExponent} знаков после разделителя`;
  return (
    <div className="space-y-7">
      {/* Заголовок */}
      <div>
        <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-1">
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
              className="inline-flex h-7 items-center rounded-[var(--radius-2)] border border-[var(--color-hairline)] bg-bg-2 px-3 font-display text-[12px] text-bg-9"
            >
              {item}
            </span>
          ))}
        </div>
        <p className="mt-2 text-[12px] text-bg-8">
          Эти параметры одинаковы для всех кампаний кабинета и не редактируются.
        </p>
      </section>

      {/* Бюджет */}
      <section>
        <SectionLabel>БЮДЖЕТ</SectionLabel>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Select
            label="Уровень бюджета"
            options={BUDGET_LEVEL_OPTIONS}
            value={values.budget_level}
            onChange={(e) =>
              onChange({ budget_level: e.target.value as WizardGoal["budget_level"] })
            }
          />
          <CurrencyAmountInput
            label={`Дневной бюджет (${currencyLabel})`}
            value={values.daily_budget}
            onValue={(daily_budget) => onChange({ daily_budget })}
            errorMessage={errors.daily_budget}
            helpText={
              currency
                ? `Hard cap: 100 000 ${currency} / день · ${precisionLabel}`
                : "Сначала подтвердите кабинет"
            }
            placeholder="Введите сумму"
          />
          {/* Целевой CPA = bid_amount для COST_CAP (обязателен) */}
          <CurrencyAmountInput
            label={`Целевой CPA (${currencyLabel})`}
            value={values.bid_amount}
            onValue={(bid_amount) => onChange({ bid_amount })}
            errorMessage={errors.bid_amount}
            helpText={`Cost cap — цена за результат · ${precisionLabel}`}
            placeholder="Из оффера или вручную"
          />
        </div>
      </section>

      {/* Таргет */}
      <section>
        <SectionLabel>ТАРГЕТ</SectionLabel>
        <div className="space-y-4">
          <div>
            <div className="text-[12px] font-display tracking-wider uppercase text-bg-9 mb-1.5">
              Страны <span className="text-bg-8">(AQ добавляется автоматически)</span>
            </div>
            <CountryMultiSelect
              id="countries"
              aria-label="Страны"
              placeholder="Начните вводить — напр. Гана"
              values={values.countries}
              onChange={(v) => onChange({ countries: v })}
              errorMessage={errors.countries}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 items-end sm:grid-cols-3">
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Дата старта"
            type="date"
            value={values.start_date}
            onChange={(e) => onChange({ start_date: e.target.value })}
            helpText="По умолчанию — следующий календарный день кабинета"
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
            <div className="text-[12px] font-display tracking-wider uppercase text-bg-9">
              URL Tags
            </div>
            <div className="text-[12px] text-bg-8 italic">
              Трекинг по SOP — бэк вычисляет автоматически
            </div>
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

/** Exact major-unit input; conversion to Meta minor units happens only server-side. */
function CurrencyAmountInput({
  label,
  value,
  onValue,
  errorMessage,
  helpText,
  placeholder,
}: {
  label: string;
  value: string;
  onValue: (value: string) => void;
  errorMessage?: string;
  helpText?: string;
  placeholder?: string;
}) {
  return (
    <Input
      label={label}
      type="text"
      inputMode="decimal"
      value={value}
      maxLength={32}
      placeholder={placeholder}
      onChange={(e) => onValue(e.target.value)}
      errorMessage={errorMessage}
      helpText={helpText}
    />
  );
}

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="font-display text-[12px] tracking-[0.14em] uppercase text-bg-8 mb-3">
      {children}
    </div>
  );
}

// ─── Валидация ────────────────────────────────────────────────────────────────

const MAJOR_AMOUNT_PATTERN = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

function validateMajorAmount(
  value: string,
  options: {
    exponent: number | null;
    label: string;
    maxWhole?: bigint;
  },
): string | null {
  const { exponent, label, maxWhole } = options;
  if (!value) return `Укажите ${label}`;
  if (!MAJOR_AMOUNT_PATTERN.test(value)) {
    return "Используйте положительное число с точкой";
  }
  if (!/[1-9]/.test(value)) return `${label} должен быть больше нуля`;
  if (exponent == null) return "Сначала подтвердите валютный контекст кабинета";

  const [wholePart, fraction = ""] = value.split(".");
  const whole = wholePart ?? "0";
  if (fraction.slice(exponent).replaceAll("0", "") !== "") {
    return exponent === 0
      ? "Для этой валюты разрешены только целые единицы"
      : `Для этой валюты допустимо не более ${exponent} знаков после точки`;
  }
  if (maxWhole != null) {
    const wholeUnits = BigInt(whole);
    const aboveCap =
      wholeUnits > maxWhole ||
      (wholeUnits === maxWhole && fraction.slice(0, exponent).replaceAll("0", "") !== "");
    if (aboveCap) return `Максимум ${maxWhole.toLocaleString("ru-RU")} в день`;
  }
  return null;
}

export function validateGoal(
  values: WizardGoal,
  currencyExponent: number | null,
): Partial<Record<keyof WizardGoal, string>> {
  // url_tags убран из WizardGoal — бэк вычисляет по SOP
  const errors: Partial<Record<keyof WizardGoal, string>> = {};

  if (!values.destination_link.trim()) errors.destination_link = "Укажите трекинг-ссылку";
  const dailyBudgetError = validateMajorAmount(values.daily_budget, {
    exponent: currencyExponent,
    label: "дневной бюджет",
    maxWhole: 100_000n,
  });
  if (dailyBudgetError) errors.daily_budget = dailyBudgetError;
  const bidAmountError = validateMajorAmount(values.bid_amount, {
    exponent: currencyExponent,
    label: "целевой CPA",
  });
  if (bidAmountError) errors.bid_amount = bidAmountError;
  if (values.countries.length === 0) errors.countries = "Укажите хотя бы одну страну";
  if (values.start_date && !/^\d{4}-\d{2}-\d{2}$/.test(values.start_date)) {
    errors.start_date = "Некорректная дата";
  }

  return errors;
}
