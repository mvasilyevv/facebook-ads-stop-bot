/**
 * RulesDrawer — right-side Drawer быстрого редактирования стоп-правил оффера.
 *
 * Открывается по кнопке «Правила» в OfferCard. Показывает то же ядро money-настроек
 * (CPA + ползунки чувствительности + live-разбивка), что и форма оффера.
 */

import { useEffect, useRef, useState } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { toast } from "@/components/ui/Toast";
import { useOfferRules, useUpdateOfferRules } from "@/lib/api/offers";
import {
  OfferRulesFields,
  DEFAULT_OFFER_RULES_VALUES,
  rulesValuesToPayload,
  rulesValuesFromOut,
  type OfferRulesValues,
} from "./OfferRulesFields";
import type { Offer } from "@fb/shared";

interface RulesDrawerProps {
  offer: Offer | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Взвод «Сохранить правила» держится ограниченное время — истёк тап, значит
// оператор передумал, а не подтвердил случайно спустя минуту.
const SAVE_ARM_TIMEOUT_MS = 5_000;

export function RulesDrawer({ offer, open, onOpenChange }: RulesDrawerProps) {
  const offerId = offer?.id ?? "";

  const { data: rules, isLoading, isError, error, refetch } = useOfferRules(offerId);
  const updateMutation = useUpdateOfferRules(offerId);

  const [values, setValues] = useState<OfferRulesValues>(DEFAULT_OFFER_RULES_VALUES);
  const [saveArmed, setSaveArmed] = useState(false);
  const armTimeoutRef = useRef<number | null>(null);

  function clearArmTimeout() {
    if (armTimeoutRef.current !== null) {
      window.clearTimeout(armTimeoutRef.current);
      armTimeoutRef.current = null;
    }
  }

  // Подтягиваем серверные значения при загрузке / смене оффера.
  useEffect(() => {
    setValues(rulesValuesFromOut(rules));
  }, [rules, offerId]);

  // Смена оффера или закрытие drawer сбрасывает взвод — иначе можно случайно
  // подтвердить сохранение чужих порогов вторым тапом.
  useEffect(() => {
    setSaveArmed(false);
    clearArmTimeout();
  }, [offerId, open]);

  useEffect(() => clearArmTimeout, []);

  async function handleSave() {
    // Двухфазная кнопка: первый клик только взводит подтверждение, реальное
    // сохранение стоп-порогов — вторым кликом.
    if (!saveArmed) {
      setSaveArmed(true);
      clearArmTimeout();
      armTimeoutRef.current = window.setTimeout(() => setSaveArmed(false), SAVE_ARM_TIMEOUT_MS);
      return;
    }
    clearArmTimeout();
    setSaveArmed(false);
    await updateMutation.mutateAsync(rulesValuesToPayload(values));
    toast.success("Правила сохранены");
    onOpenChange(false);
  }

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      eyebrow={offer ? `ПРАВИЛА · ${offer.code}` : "ПРАВИЛА"}
      title={offer ? `Пороги для ${offer.code}` : "Правила оффера"}
      description="Observer применяет правила на каждом скане."
      width={480}
    >
      <div className="px-8 py-6 overflow-y-auto flex-1">
        {isLoading && (
          <div className="flex flex-col gap-4">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} height={54} />
            ))}
          </div>
        )}

        {isError && <ErrorState error={error} onRetry={() => void refetch()} />}

        {!isLoading && !isError && (
          <>
            <OfferRulesFields
              values={values}
              onChange={(patch) => setValues((v) => ({ ...v, ...patch }))}
              disabled={updateMutation.isPending}
            />
            <div className="flex items-center gap-2 justify-end mt-6">
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={updateMutation.isPending}
              >
                Отмена
              </Button>
              <Button
                type="button"
                variant="primary"
                loading={updateMutation.isPending}
                onClick={() => void handleSave()}
              >
                {saveArmed ? "Подтвердить сохранение" : "Сохранить правила"}
              </Button>
            </div>
          </>
        )}
      </div>
    </Drawer>
  );
}
