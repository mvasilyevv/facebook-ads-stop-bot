/**
 * Offers/$id — standalone редактор правил для конкретного оффера.
 *
 * Маршрут: /offers/$id
 * Показывает: PageHeader с кодом оффера + RulesForm (6 порогов).
 * При успехе — навигация обратно на /offers.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";

import { useOffers, useOfferRules, useUpdateOfferRules } from "@/lib/api/offers";
import { RulesForm } from "@/components/offers/RulesForm";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import type { OfferRules } from "@fb/shared";

export const Route = createFileRoute("/offers/$id")({
  component: OfferRulesPage,
});

function OfferRulesPage() {
  const params = Route.useParams() as unknown as { id: string };
  const id = params.id;
  const navigate = useNavigate();

  // Загружаем список офферов для отображения кода (title)
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

  async function handleSave(values: Partial<OfferRules>) {
    await updateMutation.mutateAsync(values);
    void navigate({ to: "/" });
  }

  function handleBack() {
    void navigate({ to: "/" });
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
        eyebrowNum="02"
        eyebrow={offer ? `CATALOG · ${offer.code} · ПРАВИЛА` : "CATALOG · ПРАВИЛА"}
        title={offer ? offer.code : "Правила оффера"}
      />

      {/* ── Content ── */}
      {rulesLoading && (
        <div className="flex flex-col gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton key={i} height={54} />
          ))}
        </div>
      )}

      {rulesError && (
        <ErrorState error={error} onRetry={() => void refetch()} />
      )}

      {!rulesLoading && !rulesError && (
        <RulesForm
          rules={rules}
          loading={rulesLoading}
          saving={updateMutation.isPending}
          onSave={handleSave}
          onCancel={handleBack}
        />
      )}
    </div>
  );
}
