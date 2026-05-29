/**
 * RulesForm — stateful форма с 6 порогами оффера.
 * Используется и в RulesDrawer, и в странице /offers/$id.
 * Не делает setState в useEffect — форма инициализируется lazy initializer.
 */

import { useState } from "react";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useUpsertOfferRules } from "@/lib/api/offers";
import type { OfferRules } from "@/lib/types/api";

export const RULE_FIELDS: Array<{
  key: keyof Omit<OfferRules, "offer_id">;
  label: string;
  help: string;
}> = [
  {
    key: "spend_no_event_threshold",
    label: "Spend без события ($)",
    help: "Стоп при spend > порога без зарегистрированного события.",
  },
  {
    key: "cpa_threshold",
    label: "CPA порог ($)",
    help: "Стоп при CPA > порога.",
  },
  {
    key: "cpm_threshold",
    label: "CPM порог ($)",
    help: "Стоп при CPM > порога.",
  },
  {
    key: "ctr_threshold",
    label: "CTR порог (0-1)",
    help: "Warning при CTR ниже порога (например 0.005).",
  },
  {
    key: "frequency_threshold",
    label: "Frequency порог",
    help: "Стоп при frequency > порога.",
  },
  {
    key: "funnel_ratio_threshold",
    label: "Funnel ratio порог",
    help: "Стоп при регрессии воронки ниже порога.",
  },
];

/** Строковое представление полей в форме (пустая строка = null). */
export type RulesFormState = Record<keyof Omit<OfferRules, "offer_id">, string>;

export function rulesFromData(rules: OfferRules): RulesFormState {
  return {
    spend_no_event_threshold: rules.spend_no_event_threshold?.toString() ?? "",
    cpa_threshold: rules.cpa_threshold?.toString() ?? "",
    cpm_threshold: rules.cpm_threshold?.toString() ?? "",
    ctr_threshold: rules.ctr_threshold?.toString() ?? "",
    frequency_threshold: rules.frequency_threshold?.toString() ?? "",
    funnel_ratio_threshold: rules.funnel_ratio_threshold?.toString() ?? "",
  };
}

function emptyForm(): RulesFormState {
  return {
    spend_no_event_threshold: "",
    cpa_threshold: "",
    cpm_threshold: "",
    ctr_threshold: "",
    frequency_threshold: "",
    funnel_ratio_threshold: "",
  };
}

/**
 * Пустая строка → null, иначе числовая строка (backend ожидает Decimal как string).
 * OfferRules.threshold-поля имеют тип string | null (Pydantic Decimal → JSON string).
 */
export function parseRuleField(v: string): string | null {
  if (!v.trim()) return null;
  const n = Number.parseFloat(v);
  return Number.isNaN(n) ? null : String(n);
}

interface RulesFormProps {
  offerId: string;
  /** Если undefined — форма начинается пустой. */
  initialRules?: OfferRules;
  onClose: () => void;
  /** Если true — кнопка Отмена скрыта (standalone-режим страницы $id). */
  hideCancel?: boolean;
  saveLabel?: string;
}

export function RulesForm({
  offerId,
  initialRules,
  onClose,
  hideCancel = false,
  saveLabel = "Сохранить",
}: RulesFormProps) {
  const [form, setForm] = useState<RulesFormState>(
    () => (initialRules ? rulesFromData(initialRules) : emptyForm()),
  );

  const upsert = useUpsertOfferRules();

  function handleSave() {
    const data: Partial<OfferRules> = {
      spend_no_event_threshold: parseRuleField(form.spend_no_event_threshold),
      cpa_threshold: parseRuleField(form.cpa_threshold),
      cpm_threshold: parseRuleField(form.cpm_threshold),
      ctr_threshold: parseRuleField(form.ctr_threshold),
      frequency_threshold: parseRuleField(form.frequency_threshold),
      funnel_ratio_threshold: parseRuleField(form.funnel_ratio_threshold),
    };
    upsert.mutate(
      { id: offerId, data },
      {
        onSuccess: () => {
          toast.success("Правила сохранены", "Пороги оффера обновлены.");
          onClose();
        },
        onError: (err) =>
          toast.error("Ошибка сохранения", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* 6 числовых порогов */}
      <div className="flex-1 flex flex-col gap-4">
        {RULE_FIELDS.map((field) => (
          <Input
            key={field.key}
            id={`rule-${field.key}`}
            type="number"
            min={0}
            step="any"
            label={field.label}
            helpText={field.help}
            placeholder="—"
            value={form[field.key]}
            onChange={(e) =>
              setForm((p) => ({ ...p, [field.key]: e.target.value }))
            }
          />
        ))}
      </div>

      {/* Кнопки */}
      <div className="flex justify-end gap-2 pt-4 border-t border-bg-5">
        {!hideCancel ? (
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
        ) : null}
        <Button variant="primary" loading={upsert.isPending} onClick={handleSave}>
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}
