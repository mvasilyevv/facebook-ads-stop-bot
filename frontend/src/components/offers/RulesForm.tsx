/**
 * RulesForm — порог частоты показов (выгорание аудитории).
 * CPA — в форме оффера, чувствительность стоп/ворнинг — в SensitivityDrawer.
 * Backend PUT /offers/{id}/rules — partial: шлём ТОЛЬКО frequency_threshold,
 * остальные поля (CPA, чувствительность) не трогаем.
 */

import { useState } from "react";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useUpsertOfferRules } from "@/lib/api/offers";
import type { OfferRules } from "@/lib/types/api";

/** Описание видимых порогов формы (сейчас только частота — остальное вынесено). */
export const RULE_FIELDS = [
  {
    key: "frequency_threshold" as const,
    label: "Частота показов",
    help: "Стоп, когда среднее число показов на пользователя выше порога. Обычно 3–7.",
  },
];

/** Пустая строка → null, иначе числовая строка (backend ждёт Decimal как string). */
export function parseRuleField(v: string): string | null {
  if (!v.trim()) return null;
  const n = Number.parseFloat(v);
  return Number.isNaN(n) ? null : String(n);
}

interface RulesFormProps {
  offerId: string;
  /** Текущие правила оффера (для инициализации частоты). */
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
  const [frequency, setFrequency] = useState<string>(
    () => initialRules?.frequency_threshold?.toString() ?? "",
  );
  const [error, setError] = useState<string | undefined>();

  const upsert = useUpsertOfferRules();

  function handleSave() {
    const v = frequency.trim();
    if (v) {
      const n = Number.parseFloat(v);
      if (Number.isNaN(n) || n < 0) {
        setError("Введите неотрицательное число");
        toast.error("Проверьте поле", "Частота задана некорректно.");
        return;
      }
    }
    setError(undefined);
    // Partial: шлём ТОЛЬКО частоту — CPA и чувствительность остаются нетронутыми.
    upsert.mutate(
      { id: offerId, data: { frequency_threshold: parseRuleField(frequency) } },
      {
        onSuccess: () => toast.success("Сохранено", "Порог частоты обновлён."),
        onError: (err) =>
          toast.error("Ошибка сохранения", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      <p className="text-[12px] text-bg-9 leading-relaxed">
        Частота показов — порог выгорания аудитории. Пусто — правило выключено. CPA задаётся
        в карточке оффера, чувствительность стопа/ворнинга — кнопкой «Чувствительность».
      </p>

      <Input
        id="rule-frequency_threshold"
        type="number"
        min={0}
        step="any"
        label="Частота показов"
        helpText="Стоп, когда среднее число показов на пользователя выше порога. Обычно 3–7."
        errorMessage={error}
        placeholder="Не задано"
        value={frequency}
        onChange={(e) => setFrequency(e.target.value)}
      />

      <div className="flex justify-end gap-2 pt-4 border-t border-bg-5 mt-auto">
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
