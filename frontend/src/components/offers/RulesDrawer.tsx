/**
 * RulesDrawer — правый drawer для редактирования 6 порогов оффера.
 * Поля: spend_no_event_threshold / cpa_threshold / cpm_threshold /
 *       ctr_threshold / frequency_threshold / funnel_ratio_threshold.
 * Все числовые, nullable. Пустая строка → null при сохранении.
 *
 * Паттерн загрузки данных: форма инициализируется через useState lazy initializer
 * из rulesQuery.data, без setState в useEffect — нет cascade re-renders.
 * Сброс при смене оффера через key={offer?.id}.
 */

import { Drawer } from "@/components/ui/Drawer";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { useOfferRules } from "@/lib/api/offers";
import type { Offer } from "@/lib/types/api";
import { RulesForm } from "./RulesForm";

interface RulesDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  offer: Offer | null;
}

const FIELD_COUNT = 8;

export function RulesDrawer({ open, onOpenChange, offer }: RulesDrawerProps) {
  const rulesQuery = useOfferRules(open ? (offer?.id ?? null) : null);

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={offer ? `Правила — ${offer.code}` : "Правила оффера"}
      description={offer?.vertical ?? undefined}
      width={480}
    >
      {rulesQuery.isLoading ? (
        /* Skeleton-загрузка */
        <div className="flex flex-col gap-5">
          {Array.from({ length: FIELD_COUNT }).map((_, i) => (
            <div key={i} className="flex flex-col gap-1.5">
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
        /* key по offer.id сбрасывает форму при смене оффера */
        <RulesForm
          key={offer?.id ?? "no-offer"}
          offerId={offer?.id ?? ""}
          initialRules={rulesQuery.data}
          onClose={() => onOpenChange(false)}
        />
      )}
    </Drawer>
  );
}
