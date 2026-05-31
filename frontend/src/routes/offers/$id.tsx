/**
 * /offers/$id — standalone страница правил конкретного оффера.
 * Использует RulesForm (общий компонент) без дублирования логики.
 */

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";

import { RulesForm, RULE_FIELDS } from "@/components/offers/RulesForm";
import { useOfferRules, useOffers } from "@/lib/api/offers";
import type { Offer } from "@/lib/types/api";

export const Route = createFileRoute("/offers/$id")({
  component: OfferRulesPage,
});

function OfferRulesPage() {
  const { id } = Route.useParams();
  const navigate = useNavigate();

  // Ищем оффер в кешированном списке (include_inactive=true).
  const offersQuery = useOffers(true);
  const offer: Offer | undefined = offersQuery.data?.find((o) => o.id === id);

  const rulesQuery = useOfferRules(id);

  function handleClose() {
    navigate({ to: "/offers" });
  }

  return (
    <>
      <PageHeader
        eyebrowNum="03"
        eyebrow="КАТАЛОГ · ОФФЕР"
        title={offer ? `${offer.code} — Правила` : offersQuery.isLoading ? "Загрузка…" : "Правила оффера"}
        displayNumber=""
        subtitle={offer?.name ?? "Редактор правил"}
        action={
          <Button
            variant="ghost"
            leftIcon={<ArrowLeft size={14} aria-hidden="true" />}
            onClick={handleClose}
          >
            К офферам
          </Button>
        }
      />

      <div className="max-w-[600px]">
        {rulesQuery.isLoading ? (
          /* Skeleton пока загружаются правила */
          <div className="flex flex-col gap-5">
            {RULE_FIELDS.map((f) => (
              <div key={f.key} className="flex flex-col gap-1.5">
                <Skeleton height={11} width={140} />
                <Skeleton height={32} />
              </div>
            ))}
          </div>
        ) : rulesQuery.isError ? (
          <ErrorState
            error={rulesQuery.error}
            onRetry={() => rulesQuery.refetch()}
          />
        ) : (
          /* key по id сбрасывает форму при смене URL */
          <RulesForm
            key={id}
            offerId={id}
            initialRules={rulesQuery.data}
            onClose={handleClose}
            saveLabel="Сохранить правила"
          />
        )}
      </div>
    </>
  );
}
