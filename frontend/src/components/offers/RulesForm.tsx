/**
 * RulesForm — форма редактирования 6 числовых порогов оффера.
 *
 * Пороги (OfferRuleOut):
 *   spend_no_event_threshold  — spend без событий (стоп)
 *   cpa_threshold             — CPA (cost per action/lead)
 *   cpm_threshold             — CPM
 *   ctr_threshold             — CTR (%)
 *   frequency_threshold       — частота показов
 *   funnel_ratio_threshold    — funnel ratio (leads/regs)
 *
 * Все поля nullable — null означает «правило неактивно».
 * Бэк принимает числа (или null) через PUT /offers/{id}/rules.
 *
 * Используется как в RulesDrawer, так и на standalone-странице /offers/{id}.
 */

import { useEffect, useState } from "react";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import type { OfferRules } from "@fb/shared";

// ─── Описания полей ───────────────────────────────────────────────────────────

interface FieldMeta {
  key: keyof Omit<OfferRules, "offer_id">;
  label: string;
  placeholder: string;
  unit: string;
  help: string;
}

const RULE_FIELDS: FieldMeta[] = [
  {
    key: "spend_no_event_threshold",
    label: "Spend без события",
    placeholder: "50",
    unit: "$",
    help: "Стоп при spend выше порога без leads/regs/deps",
  },
  {
    key: "cpa_threshold",
    label: "CPA порог",
    placeholder: "25",
    unit: "$",
    help: "Cost per action (lead/reg). Стоп при превышении",
  },
  {
    key: "cpm_threshold",
    label: "CPM порог",
    placeholder: "10",
    unit: "$",
    help: "Cost per 1000 impressions. Стоп при превышении",
  },
  {
    key: "ctr_threshold",
    label: "CTR порог",
    placeholder: "1.5",
    unit: "%",
    help: "Click-through rate. Стоп при значении ниже порога",
  },
  {
    key: "frequency_threshold",
    label: "Frequency порог",
    placeholder: "3.0",
    unit: "×",
    help: "Средняя частота показа. Стоп при превышении (opt-in)",
  },
  {
    key: "funnel_ratio_threshold",
    label: "Funnel ratio",
    placeholder: "0.3",
    unit: "",
    help: "Leads ÷ Regs. Стоп при значении ниже порога",
  },
];

// ─── Хелперы ─────────────────────────────────────────────────────────────────

/** Конвертирует строковое значение из OfferRuleOut в строку для инпута. */
function ruleValToStr(val: string | null | undefined): string {
  if (val === null || val === undefined) return "";
  return val;
}

/** Конвертирует строку инпута в значение для PUT (null если пусто). */
function strToRuleVal(str: string): string | null {
  const trimmed = str.trim();
  if (!trimmed) return null;
  return trimmed;
}

// ─── Типы ────────────────────────────────────────────────────────────────────

type RuleFormState = Record<string, string>;

interface RulesFormProps {
  /** Текущие правила (null при загрузке). */
  rules: OfferRules | null | undefined;
  /** Идёт ли загрузка правил. */
  loading?: boolean;
  /** Идёт ли сохранение. */
  saving?: boolean;
  onSave: (values: Partial<OfferRules>) => Promise<void>;
  /** Кнопка отмены (опционально — для standalone-режима). */
  onCancel?: () => void;
}

// ─── Компонент ────────────────────────────────────────────────────────────────

export function RulesForm({ rules, loading, saving, onSave, onCancel }: RulesFormProps) {
  // Внутреннее состояние формы — строки (пустая = null-threshold)
  const [formState, setFormState] = useState<RuleFormState>({});

  // Синхронизируем с данными из API
  useEffect(() => {
    if (!rules) return;
    const state: RuleFormState = {};
    for (const field of RULE_FIELDS) {
      state[field.key] = ruleValToStr(rules[field.key] as string | null | undefined);
    }
    setFormState(state);
  }, [rules]);

  function handleChange(key: string, value: string) {
    setFormState((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    // Собираем payload: пустые поля → null
    const payload: Partial<OfferRules> = {};
    for (const field of RULE_FIELDS) {
      (payload as Record<string, string | null>)[field.key] = strToRuleVal(
        formState[field.key] ?? "",
      );
    }
    await onSave(payload);
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} noValidate aria-label="Правила оффера">
      {/* Eyebrow-пояснение */}
      <p className="font-display text-[11px] text-bg-9 tracking-[0.02em] mb-5">
        Пустое поле = правило неактивно для этого оффера.
        <br />
        Observer применяет активные пороги при каждом скане.
      </p>

      <div className="flex flex-col gap-4 mb-6">
        {RULE_FIELDS.map((field) => (
          <RuleField
            key={field.key}
            meta={field}
            value={formState[field.key] ?? ""}
            onChange={(val) => handleChange(field.key, val)}
            disabled={loading || saving}
          />
        ))}
      </div>

      <div className="flex items-center gap-2 justify-end">
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
            Отмена
          </Button>
        ) : null}
        <Button type="submit" variant="primary" loading={saving} disabled={loading || saving}>
          Сохранить правила
        </Button>
      </div>
    </form>
  );
}

// ─── Поле правила ─────────────────────────────────────────────────────────────

function RuleField({
  meta,
  value,
  onChange,
  disabled,
}: {
  meta: FieldMeta;
  value: string;
  onChange: (val: string) => void;
  disabled?: boolean;
}) {
  const isEmpty = !value.trim();

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label
          htmlFor={`rule-${meta.key}`}
          className="font-display text-[11px] tracking-wider uppercase text-bg-9"
        >
          {meta.label}
          {meta.unit && (
            <span className="ml-1 text-bg-7 normal-case tracking-normal">{meta.unit}</span>
          )}
        </label>
        {isEmpty && (
          <span className="font-display text-[9px] tracking-[0.12em] uppercase text-bg-7">
            inactive
          </span>
        )}
      </div>
      <Input
        id={`rule-${meta.key}`}
        type="number"
        step="any"
        min="0"
        placeholder={isEmpty ? meta.placeholder : undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        helpText={meta.help}
        size="md"
      />
    </div>
  );
}
