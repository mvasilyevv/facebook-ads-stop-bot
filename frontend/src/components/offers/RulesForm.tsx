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
  /** Поля-доли (0–1): значение > 1 почти наверняка ошибка (проценты вместо доли). */
  fraction?: boolean;
}> = [
  {
    key: "spend_no_event_threshold",
    label: "Расход без событий, $",
    help: "Стоп, когда расход превысил порог, а событий (лидов) ещё нет.",
  },
  {
    key: "cpa_threshold",
    label: "CPA, $",
    help: "Стоп, когда стоимость целевого действия выше порога.",
  },
  {
    key: "cpm_threshold",
    label: "CPM, $",
    help: "Стоп, когда цена за 1000 показов выше порога.",
  },
  {
    key: "ctr_threshold",
    label: "CTR, доля 0–1",
    help: "Warning при CTR ниже порога. Указывайте долей: 0.02 = 2%.",
    fraction: true,
  },
  {
    key: "frequency_threshold",
    label: "Частота показов",
    help: "Стоп, когда среднее число показов на пользователя выше порога. Обычно 3–7.",
  },
  {
    key: "funnel_ratio_threshold",
    label: "Коэффициент воронки, доля 0–1",
    help: "Стоп при просадке воронки ниже порога. Указывайте долей: 0.05 = 5%.",
    fraction: true,
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
  const [errors, setErrors] = useState<Partial<RulesFormState>>({});

  const upsert = useUpsertOfferRules();

  /** Проверка значений: число, не отрицательное, для долей — не больше 1. */
  function validate(): boolean {
    const next: Partial<RulesFormState> = {};
    for (const f of RULE_FIELDS) {
      const v = form[f.key].trim();
      if (!v) continue;
      const n = Number.parseFloat(v);
      if (Number.isNaN(n)) next[f.key] = "Введите число";
      else if (n < 0) next[f.key] = "Не может быть отрицательным";
      else if (f.fraction && n > 1) next[f.key] = "Доля от 0 до 1 (например 0.02 = 2%)";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function handleSave() {
    if (!validate()) {
      toast.error("Проверьте поля", "Некоторые пороги заданы некорректно.");
      return;
    }
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
        // Drawer не закрываем — пользователь может проверить результат и править дальше.
        onSuccess: () => toast.success("Правила сохранены", "Пороги оффера обновлены."),
        onError: (err) =>
          toast.error("Ошибка сохранения", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      <p className="text-[12px] text-bg-9 leading-relaxed">
        Пороги авто-отключения объявлений этого оффера. Пустое поле — правило выключено.
      </p>

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
            errorMessage={errors[field.key]}
            placeholder="Не задано"
            value={form[field.key]}
            onChange={(e) => setForm((p) => ({ ...p, [field.key]: e.target.value }))}
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
