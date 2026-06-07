/**
 * RulesDrawer — right-side Drawer с RulesForm для редактирования порогов оффера.
 *
 * Используется на /offers: открывается по кнопке «Правила» в OfferCard.
 * Состояние загрузки и сохранения — через useOfferRules / useUpdateOfferRules.
 */

import { Drawer } from "@/components/ui/Drawer";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useOfferRules, useUpdateOfferRules } from "@/lib/api/offers";
import { RulesForm } from "./RulesForm";
import type { Offer, OfferRules } from "@fb/shared";

interface RulesDrawerProps {
  offer: Offer | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RulesDrawer({ offer, open, onOpenChange }: RulesDrawerProps) {
  const offerId = offer?.id ?? "";

  const {
    data: rules,
    isLoading,
    isError,
    error,
    refetch,
  } = useOfferRules(offerId);

  const updateMutation = useUpdateOfferRules(offerId);

  async function handleSave(values: Partial<OfferRules>) {
    await updateMutation.mutateAsync(values);
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
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <Skeleton key={i} height={54} />
            ))}
          </div>
        )}

        {isError && (
          <ErrorState error={error} onRetry={() => void refetch()} />
        )}

        {!isLoading && !isError && (
          <RulesForm
            rules={rules}
            loading={isLoading}
            saving={updateMutation.isPending}
            onSave={handleSave}
            onCancel={() => onOpenChange(false)}
          />
        )}
      </div>
    </Drawer>
  );
}
