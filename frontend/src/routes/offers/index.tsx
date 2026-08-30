/**
 * Offers — страница офферов (сетка карточек + правила + CRUD).
 *
 * Компоновка:
 *   PageHeader eyebrow "02" / "CATALOG · ОФФЕРЫ"
 *   Toolbar: active-state filter + [+ Создать]
 *   Сетка OfferCard с подтверждённой catalog-конфигурацией
 *   RulesDrawer (6 порогов)
 *   OfferFormModal (создание/редактирование)
 *   ConfirmDialog (delete)
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Tag, Plus } from "lucide-react";
import { safeApiProblemMessage } from "@fb/operator-api";

import {
  useOffers,
  useCreateOffer,
  useUpdateOffer,
  useDeactivateOffer,
  type Offer,
} from "@/lib/api/offers";
import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { RulesDrawer } from "@/components/offers/RulesDrawer";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { FilterPill } from "@/components/ui/Pill";
import { EmptyState } from "@/components/ui/EmptyState";
import { OperatorUnavailableState } from "@/components/layout/OperatorPageBoundary";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { russianCountForm } from "@fb/shared";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

// ─── Tab фильтр ───────────────────────────────────────────────────────────────

type OfferTab = "all" | "active" | "inactive";
const TAB_LABELS: Record<OfferTab, string> = {
  all: "Все",
  active: "Активные",
  inactive: "Неактивные",
};

// ─── Компонент ────────────────────────────────────────────────────────────────

function OffersPage() {
  const [tab, setTab] = useState<OfferTab>("all");

  // CRUD state
  const [createOpen, setCreateOpen] = useState(false);
  const [editOffer, setEditOffer] = useState<Offer | null>(null);
  const [rulesOffer, setRulesOffer] = useState<Offer | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [offerToDeactivate, setOfferToDeactivate] = useState<Offer | null>(null);

  // API — всегда includeInactive=true, фильтруем локально по tab
  const { data: offers, isLoading, isError, error, refetch } = useOffers(true);

  // Мутации — один экземпляр на страницу
  const createMutation = useCreateOffer();

  function handleOpenRules(offer: Offer) {
    setRulesOffer(offer);
    setRulesOpen(true);
  }

  function handleOpenEdit(offer: Offer) {
    setEditOffer(offer);
  }

  function handleOpenDeactivate(offer: Offer) {
    setOfferToDeactivate(offer);
  }

  // ── Skeleton ──
  if (isLoading) {
    return (
      <div>
        <OffersHeader count={null} />
        <div className="grid grid-cols-3 gap-4 mt-8">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} variant="block" height={240} />
          ))}
        </div>
      </div>
    );
  }

  // ── Error ──
  if (isError) {
    return (
      <div>
        <OffersHeader count={null} />
        <OperatorUnavailableState
          title="Офферы недоступны"
          resource="каталог офферов"
          details={safeApiProblemMessage(error, "Не удалось загрузить каталог офферов.")}
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  const allOffers = offers ?? [];

  const filteredOffers = allOffers
    .filter((o) => {
      if (tab === "active") return o.is_active;
      if (tab === "inactive") return !o.is_active;
      return true;
    })
    .sort((a, b) => a.code.localeCompare(b.code, "ru"));

  return (
    <>
      {/* ── Header ── */}
      <OffersHeader
        count={allOffers.length}
        action={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Plus size={14} />}
            onClick={() => setCreateOpen(true)}
          >
            Новый оффер
          </Button>
        }
      />

      {/* ── Toolbar: tab pills ── */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {(Object.keys(TAB_LABELS) as OfferTab[]).map((t) => (
            <FilterPill key={t} active={tab === t} onClick={() => setTab(t)}>
              {TAB_LABELS[t]}
            </FilterPill>
          ))}
        </div>
      </div>

      {/* ── Empty state ── */}
      {filteredOffers.length === 0 && (
        <EmptyState
          icon={<Tag size={32} />}
          title="Офферов нет"
          description={
            tab === "all"
              ? "Создайте первый оффер — он будет матчиться с кампаниями по коду в названии."
              : `Нет ${tab === "active" ? "активных" : "неактивных"} офферов.`
          }
          action={
            tab === "all" ? (
              <Button
                variant="primary"
                size="sm"
                leftIcon={<Plus size={14} />}
                onClick={() => setCreateOpen(true)}
              >
                Новый оффер
              </Button>
            ) : undefined
          }
        />
      )}

      {/* ── Сетка офферов: 3 колонки фиксированные ── */}
      {filteredOffers.length > 0 && (
        <div
          className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
          role="list"
          aria-label="Офферы"
        >
          {filteredOffers.map((offer) => (
            <div key={offer.id} role="listitem">
              <OfferCard
                offer={offer}
                onEditOffer={handleOpenEdit}
                onEditRules={handleOpenRules}
                onDeactivate={handleOpenDeactivate}
              />
            </div>
          ))}
        </div>
      )}

      {/* ── OfferFormModal: создание ── */}
      <OfferFormModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        offer={null}
        onSave={async (values) => {
          // Создаём конфигурацию оффера. Стоп-правила (CPA + чувствительность) — отдельно
          // через кнопку «Правила» (RulesDrawer), здесь их не трогаем.
          await createMutation.mutateAsync({
            code: values.code,
            is_active: values.is_active,
            pixel_id: values.pixel_id || null, // пусто → не задан
            ad_account_ids: values.ad_account_ids, // мульти-кабинет: min 1
            countries: values.countries, // гео оффера (ISO-2 upper)
          });
          setCreateOpen(false);
          toast.success(`Оффер ${values.code} создан. Стоп-правила задайте в «Правилах».`);
        }}
      />

      {/* ── OfferFormModal: редактирование ── */}
      {editOffer && <EditOfferModal offer={editOffer} onClose={() => setEditOffer(null)} />}

      {/* ── RulesDrawer ── */}
      <RulesDrawer offer={rulesOffer} open={rulesOpen} onOpenChange={setRulesOpen} />

      {/* ── ConfirmDialog: soft deactivation ── */}
      {offerToDeactivate ? (
        <OfferDeactivateManager
          offer={offerToDeactivate}
          open
          onOpenChange={(open) => {
            if (!open) setOfferToDeactivate(null);
          }}
        />
      ) : null}
    </>
  );
}

// ─── EditOfferModal — wrapper с per-offer хуком ───────────────────────────────

/**
 * Выносим редактирование в отдельный компонент, т.к. useUpdateOffer принимает
 * offerId в конструкторе хука и не может быть вызван условно внутри OffersPage.
 */
function EditOfferModal({ offer, onClose }: { offer: Offer; onClose: () => void }) {
  const updateMutation = useUpdateOffer(offer.id);

  return (
    <OfferFormModal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      offer={offer}
      onSave={async (values) => {
        // Стоп-правила редактируются отдельно в «Правилах».
        await updateMutation.mutateAsync({
          is_active: values.is_active,
          pixel_id: values.pixel_id, // строка (в т.ч. "") — форма источник истины
          ad_account_ids: values.ad_account_ids, // мульти-кабинет: замена списка
          countries: values.countries, // гео оффера (ISO-2 upper) — замена списка
        });
        onClose();
      }}
    />
  );
}

export function OfferDeactivateManager({
  offer,
  open,
  onOpenChange,
}: {
  offer: Offer;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const deactivateMutation = useDeactivateOffer();

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Деактивировать оффер ${offer.code}?`}
      description="Оффер будет помечен как неактивный. Исторические данные сохранятся."
      confirmWord={offer.code}
      confirmLabel="Деактивировать"
      confirmVariant="danger"
      onConfirm={async () => {
        await deactivateMutation.mutateAsync(offer.id);
        toast.success(`Оффер ${offer.code} деактивирован`);
        onOpenChange(false);
      }}
    />
  );
}

// ─── PageHeader ────────────────────────────────────────────────────────────────

function OffersHeader({ count, action }: { count: number | null; action?: React.ReactNode }) {
  return (
    <PageHeader
      title="Офферы"
      action={action}
      subtitle={
        count !== null ? (
          <>
            <span className="text-bg-11 font-medium">{count}</span>
            <HeaderSep />
            {russianCountForm(count, "оффер", "оффера", "офферов")} в каталоге
          </>
        ) : undefined
      }
    />
  );
}


