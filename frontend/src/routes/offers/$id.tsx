/**
 * Offers/$id — standalone редактор стоп-правил для конкретного оффера.
 *
 * Маршрут: /offers/$id
 * Показывает: PageHeader с кодом оффера + ядро money-настроек (CPA + ползунки + live-разбивка).
 * При успехе — навигация обратно на /offers.
 */

import { useEffect, useRef, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { safeApiProblemMessage } from "@fb/operator-api";

import { useOffers, useOfferRules, useUpdateOfferRules } from "@/lib/api/offers";
import {
  OfferRulesFields,
  DEFAULT_OFFER_RULES_VALUES,
  rulesValuesToPayload,
  rulesValuesFromOut,
  type OfferRulesValues,
} from "@/components/offers/OfferRulesFields";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/components/ui/Toast";

export const Route = createFileRoute("/offers/$id")({
  component: OfferRulesPage,
});

// Взвод «Сохранить правила» держится ограниченное время — истёк тап, значит
// оператор передумал, а не подтвердил случайно спустя минуту.
const SAVE_ARM_TIMEOUT_MS = 5_000;

function OfferRulesPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();

  const { data: offers } = useOffers();
  const offer = offers?.find((o) => o.id === id);

  const {
    data: rules,
    isLoading: rulesLoading,
    isError: rulesError,
    error,
    refetch,
  } = useOfferRules(id);

  const updateMutation = useUpdateOfferRules(id);

  const [values, setValues] = useState<OfferRulesValues>(DEFAULT_OFFER_RULES_VALUES);
  const [saveArmed, setSaveArmed] = useState(false);
  const armTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    setValues(rulesValuesFromOut(rules));
  }, [rules]);

  useEffect(() => {
    return () => {
      if (armTimeoutRef.current !== null) window.clearTimeout(armTimeoutRef.current);
    };
  }, []);

  async function handleSave() {
    // Двухфазная кнопка: первый клик только взводит подтверждение, реальное
    // сохранение стоп-порогов — вторым кликом.
    if (!saveArmed) {
      setSaveArmed(true);
      if (armTimeoutRef.current !== null) window.clearTimeout(armTimeoutRef.current);
      armTimeoutRef.current = window.setTimeout(() => {
        setSaveArmed(false);
        armTimeoutRef.current = null;
      }, SAVE_ARM_TIMEOUT_MS);
      return;
    }
    if (armTimeoutRef.current !== null) {
      window.clearTimeout(armTimeoutRef.current);
      armTimeoutRef.current = null;
    }
    setSaveArmed(false);
    await updateMutation.mutateAsync(rulesValuesToPayload(values));
    toast.success("Правила сохранены");
    void navigate({ to: "/offers" });
  }

  function handleBack() {
    void navigate({ to: "/offers" });
  }

  return (
    <div className="max-w-[640px]">
      {/* ── Back link ── */}
      <div className="mb-6">
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<ChevronLeft size={14} />}
          onClick={handleBack}
          aria-label="Назад к офферам"
        >
          Все офферы
        </Button>
      </div>

      {/* ── Header ── */}
      <PageHeader
        title={offer ? offer.code : "Правила оффера"}
      />

      {/* ── Content ── */}
      {rulesLoading && (
        <div className="flex flex-col gap-4">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} height={54} />
          ))}
        </div>
      )}

      {rulesError && (
        <OperatorUnavailableState
          title="Правила оффера недоступны"
          resource="правила оффера"
          details={safeApiProblemMessage(error, "Не удалось загрузить правила оффера.")}
          onRetry={() => void refetch()}
        />
      )}

      {!rulesLoading && !rulesError && (
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
              onClick={handleBack}
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
  );
}
