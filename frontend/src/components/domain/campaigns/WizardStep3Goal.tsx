/**
 * Шаг 3 — Цель / Бюджет / Таргет / Атрибуция / Назначение.
 *
 * Секции:
 *   - Цель оптимизации — read-only «Зашито по SOP» (objective/optimization_goal/
 *     custom_event_type/billing/text_optimizations не редактируются)
 *   - Стратегия ставок — выбор из четырёх стратегий Meta; поле ставки
 *     показывается только для стратегий с кэпом
 *   - Бюджет (major-unit decimal strings in the confirmed cabinet currency)
 *   - Таргет (countries+AQ, age_min/max, advantage_audience)
 *   - Атрибуция (click_through_days, view_through_days)
 *   - Назначение (destination_link, cta, start_date)
 */

import { type FC } from "react";
import {
  CAMPAIGN_BID_STRATEGY_OPTIONS,
  CAMPAIGN_GENDER_OPTIONS,
  CAMPAIGN_PLACEMENT_OPTIONS,
  validateCampaignGoal,
} from "@fb/features/campaigns";
import { CALL_TO_ACTIONS } from "@fb/shared";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { CountryMultiSelect } from "@/components/ui/CountryMultiSelect";
import type { WizardGoal } from "@/stores/campaignWizard";
import { CampaignTagPicker } from "./CampaignTagPicker";

interface WizardStep3GoalProps {
  values: WizardGoal;
  onChange: (v: Partial<WizardGoal>) => void;
  currency: string | null;
  currencyExponent: number | null;
  errors?: Partial<Record<keyof WizardGoal, string>>;
  appliedPresetName?: string | null;
}

const BUDGET_LEVEL_OPTIONS = [
  { value: "campaign", label: "CBO — бюджет на кампании" },
  { value: "adset", label: "ABO — бюджет на adset'ах" },
];

// Инварианты, зашитые по SOP — показываем read-only, без выбора в UI.
// Стратегия ставок отсюда снята: замер 17.08 по трём кабинетам показал 41
// живую кампанию из 55 на «Максимальном количестве» — «зашито по SOP» было
// неправдой, и оператор не мог повторить три четверти того, что уже работает.
const SOP_LOCKED = ["Sales", "OFFSITE_CONVERSIONS", "Purchase", "IMPRESSIONS", "OPT_OUT"];

const ATTRIBUTION_DAYS_OPTIONS = [
  { value: "1", label: "1 день" },
  { value: "7", label: "7 дней" },
  { value: "28", label: "28 дней" },
];

function asAttributionDays(value: string): 1 | 7 | 28 {
  return value === "7" ? 7 : value === "28" ? 28 : 1;
}

export const WizardStep3Goal: FC<WizardStep3GoalProps> = ({
  values,
  onChange,
  currency,
  currencyExponent,
  errors = {},
  appliedPresetName,
}) => {
  const currencyLabel = currency || "валюта не подтверждена";
  const precisionLabel =
    currencyExponent == null
      ? "Точность не подтверждена"
      : currencyExponent === 0
        ? "Только целые единицы"
        : `До ${currencyExponent} знаков после разделителя`;
  const bidStrategyOption = CAMPAIGN_BID_STRATEGY_OPTIONS.find(
    (option) => option.value === values.bid_strategy,
  );
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

      {appliedPresetName ? (
        <div
          role="status"
          className="border-y border-accent/30 bg-accent-bg/40 px-4 py-3 text-[13px] text-bg-10"
        >
          Пресет «{appliedPresetName}» подставил отмеченные параметры. Все поля ниже можно изменить
          для этого запуска.
        </div>
      ) : null}

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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            label="Уровень бюджета"
            options={BUDGET_LEVEL_OPTIONS}
            value={values.budget_level}
            onChange={(e) =>
              onChange({ budget_level: e.target.value as WizardGoal["budget_level"] })
            }
          />
          <Select
            label="Стратегия ставок"
            options={CAMPAIGN_BID_STRATEGY_OPTIONS.map(({ value, label }) => ({ value, label }))}
            value={values.bid_strategy}
            onChange={(e) =>
              onChange({ bid_strategy: e.target.value as WizardGoal["bid_strategy"] })
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
          {/* Ставка есть только у стратегий с кэпом: у «Максимального
              количества» её нет вовсе, и пустое поле сбивало бы с толку. */}
          {bidStrategyOption?.needsBid ? (
            <CurrencyAmountInput
              label={`${bidStrategyOption.label} (${currencyLabel})`}
              value={values.bid_amount}
              onValue={(bid_amount) => onChange({ bid_amount })}
              errorMessage={errors.bid_amount}
              helpText={`Предел цены за результат · ${precisionLabel}`}
              placeholder="Из оффера или вручную"
            />
          ) : null}
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

          {/* Выравнивание по верху: helpText «Возраста до» появляется только при
              Advantage+ и не должен сдвигать соседние контролы (items-end ронял
              весь ряд вниз на высоту подсказки). */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Input
              label="Возраст от"
              type="number"
              min={18}
              max={65}
              value={String(values.age_min)}
              onChange={(e) => onChange({ age_min: Number(e.target.value) })}
            />
            {/* При Advantage+ билдер форсит верхнюю границу 65 (Meta иначе
                отвергает adset). Поле показывает то, что реально уедет, а не
                выбор, который будет молча заменён. */}
            <Input
              label="Возраст до"
              type="number"
              min={18}
              max={65}
              disabled={values.advantage_audience}
              value={values.advantage_audience ? "65" : String(values.age_max)}
              helpText={
                values.advantage_audience
                  ? "Advantage+ сам расширяет аудиторию — верхнюю границу задать нельзя"
                  : undefined
              }
              onChange={(e) => onChange({ age_max: Number(e.target.value) })}
            />
            {/* Колонка повторяет структуру Input (label + контрол h-11), чтобы
                тогл стоял на одной линии с полями возраста. */}
            <div className="flex flex-col gap-1.5">
              <span className="text-[12px] font-display tracking-wider uppercase text-bg-9">
                Advantage+
              </span>
              <Switch
                checked={values.advantage_audience}
                onChange={() => onChange({ advantage_audience: !values.advantage_audience })}
                label="Advantage+ Audience"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <CampaignTagPicker
              label="Пол"
              values={values.genders}
              options={CAMPAIGN_GENDER_OPTIONS}
              emptyLabel="Все полы"
              onChange={(genders) => onChange({ genders })}
            />
            <CampaignTagPicker
              label="Плейсменты"
              values={values.placements}
              options={CAMPAIGN_PLACEMENT_OPTIONS}
              emptyLabel="Автоматические плейсменты Meta"
              onChange={(placements) => onChange({ placements })}
            />
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
            onChange={(e) => onChange({ click_through_days: asAttributionDays(e.target.value) })}
          />
          <Select
            label="View-through (дни)"
            options={ATTRIBUTION_DAYS_OPTIONS}
            value={String(values.view_through_days)}
            onChange={(e) => onChange({ view_through_days: asAttributionDays(e.target.value) })}
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
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Destination URL (трекинг-ссылка)"
            placeholder="https://tracker.example.com/click?..."
            value={values.destination_link}
            onChange={(e) => onChange({ destination_link: e.target.value })}
            errorMessage={errors.destination_link}
            helpText="Трекинг-ссылка из AdSet.pro"
          />
          {/* Meta показывает это вместо сырого домена трекера, но принимает
              только настоящий URL или домен — произвольный текст отклоняется. */}
          <Input
            label="Отображаемая ссылка"
            placeholder="play.ghana.com"
            value={values.display_link}
            onChange={(e) => onChange({ display_link: e.target.value })}
            errorMessage={errors.display_link}
            helpText="Пусто — Meta покажет домен трекинг-ссылки"
          />
        </div>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="Шаблон нейминга"
            placeholder="{byer} | {offer} | adset.pro | {date}"
            value={values.naming_template}
            onChange={(event) => onChange({ naming_template: event.target.value })}
            helpText="Пусто — стандартный шаблон. Доступны {byer}, {offer}, {type}, {date}."
          />
          {/* Плейсхолдеры вида {byer} тут не подставляются: своя строка уезжает
              буквально, раскрывает её уже Meta. Пример не должен учить обратному. */}
          <Input
            label="URL Tags"
            placeholder="sub2=mv&sub5={{campaign.name}}"
            value={values.url_tags_template}
            onChange={(event) => onChange({ url_tags_template: event.target.value })}
            helpText="Пусто — SOP-теги. Своя строка уедет буквально; sub8={{ad.id}} сервер добавит сам."
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

export function validateGoal(
  values: WizardGoal,
  currencyExponent: number | null,
): Partial<Record<keyof WizardGoal, string>> {
  return validateCampaignGoal(values, currencyExponent);
}
