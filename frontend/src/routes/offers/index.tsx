/**
 * Offers — страница офферов (сетка карточек + правила + CRUD).
 *
 * Компоновка:
 *   PageHeader eyebrow "02" / "CATALOG · ОФФЕРЫ"
 *   Toolbar: period selector (days) + toggle include_inactive + [+ Создать]
 *   Сетка 3 col OfferCard (offer summary + метрики из /offers/compare)
 *   RulesDrawer (6 порогов)
 *   OfferFormModal (создание/редактирование)
 *   ConfirmDialog (delete)
 */

import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Tag, Plus } from "lucide-react";

import {
  useOffers,
  useOffersCompare,
  useCreateOffer,
  useUpdateOffer,
  useDeleteOffer,
  type Offer,
} from "@/lib/api/offers";
import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { RulesDrawer } from "@/components/offers/RulesDrawer";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { FilterPill } from "@/components/ui/Pill";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

// ─── Tab фильтр ───────────────────────────────────────────────────────────────

type OfferTab = "all" | "active" | "inactive";
type OfferSort = "spend" | "alerts" | "name";

const TAB_LABELS: Record<OfferTab, string> = {
  all: "Все",
  active: "Активные",
  inactive: "Неактивные",
};

// ─── Компонент ────────────────────────────────────────────────────────────────

function OffersPage() {
  const [tab, setTab] = useState<OfferTab>("all");
  const [sort, setSort] = useState<OfferSort>("spend");
  const days = 7; // метрики всегда за 7 дней

  // CRUD state
  const [createOpen, setCreateOpen] = useState(false);
  const [editOffer, setEditOffer] = useState<Offer | null>(null);
  const [rulesOffer, setRulesOffer] = useState<Offer | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [deleteOffer, setDeleteOffer] = useState<Offer | null>(null);

  // API — всегда includeInactive=true, фильтруем локально по tab
  const { data: offers, isLoading, isError, error, refetch } = useOffers(true);
  const { data: compareRows } = useOffersCompare(days);

  // Мутации — один экземпляр на страницу
  const createMutation = useCreateOffer();

  function handleOpenRules(offer: Offer) {
    setRulesOffer(offer);
    setRulesOpen(true);
  }

  function handleOpenEdit(offer: Offer) {
    setEditOffer(offer);
  }

  function handleOpenDelete(offer: Offer) {
    setDeleteOffer(offer);
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
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const allOffers = offers ?? [];

  // Фильтрация по tab
  // Строим карту metrics по offer_id для быстрого доступа и честной сортировки.
  const metricsMap = new Map(compareRows?.map((r) => [r.offer_id, r]) ?? []);

  const filteredOffers = allOffers.filter((o) => {
    if (tab === "active") return o.is_active;
    if (tab === "inactive") return !o.is_active;
    return true;
  }).sort((a, b) => {
    if (sort === "name") return a.code.localeCompare(b.code, "ru");
    const left = metricsMap.get(a.id);
    const right = metricsMap.get(b.id);
    if (sort === "alerts") {
      return Number(right?.stop_alerts_count ?? 0) - Number(left?.stop_alerts_count ?? 0);
    }
    return Number(right?.spend ?? 0) - Number(left?.spend ?? 0);
  });

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

      {/* ── Toolbar: tab pills + sort ── */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          {(Object.keys(TAB_LABELS) as OfferTab[]).map((t) => (
            <FilterPill key={t} active={tab === t} onClick={() => setTab(t)}>
              {TAB_LABELS[t]}
            </FilterPill>
          ))}
        </div>
        <label className="flex items-center gap-2 font-display text-[11px] text-bg-9">
          Сортировка
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value as OfferSort)}
            className="h-8 rounded-[var(--radius-2)] border border-[var(--hairline)] bg-bg-2 px-3 font-display text-[12px] text-bg-11 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            aria-label="Сортировка офферов"
          >
            <option value="spend">по тратам</option>
            <option value="alerts">по стопам</option>
            <option value="name">по названию</option>
          </select>
        </label>
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
                metrics={metricsMap.get(offer.id)}
                onEditOffer={handleOpenEdit}
                onEditRules={handleOpenRules}
                onDelete={handleOpenDelete}
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
          // Создаём оффер (identity). Стоп-правила (CPA + чувствительность) — отдельно
          // через кнопку «Правила» (RulesDrawer), здесь их не трогаем.
          await createMutation.mutateAsync({
            code: values.code,
            name: values.code, // бэк: name=code
            is_active: values.is_active,
            pixel_id: values.pixel_id || null, // пусто → не задан
            ad_account_ids: values.ad_account_ids, // мульти-кабинет: min 1
            countries: values.countries, // гео оффера (ISO-2 upper)
          });
          setCreateOpen(false);
          toast.success(
            `Оффер ${values.code} создан. Стоп-правила задайте в «Правилах».`,
          );
        }}
      />

      {/* ── OfferFormModal: редактирование ── */}
      {editOffer && (
        <EditOfferModal
          offer={editOffer}
          onClose={() => setEditOffer(null)}
        />
      )}

      {/* ── RulesDrawer ── */}
      <RulesDrawer
        offer={rulesOffer}
        open={rulesOpen}
        onOpenChange={setRulesOpen}
      />

      {/* ── ConfirmDialog: delete ── */}
      {deleteOffer ? (
        <OfferDeleteManager
          offer={deleteOffer}
          open
          onOpenChange={(open) => {
            if (!open) setDeleteOffer(null);
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
      onOpenChange={(open) => { if (!open) onClose(); }}
      offer={offer}
      onSave={async (values) => {
        // Только identity. Стоп-правила редактируются отдельно в «Правилах».
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

export function OfferDeleteManager({
  offer,
  open,
  onOpenChange,
}: {
  offer: Offer;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const deleteMutation = useDeleteOffer();

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Удалить оффер ${offer.code}?`}
      description="Оффер будет помечен как неактивный. Исторические данные сохранятся."
      confirmWord={offer.code}
      confirmLabel="Удалить"
      confirmVariant="danger"
      onConfirm={async () => {
        await deleteMutation.mutateAsync(offer.id);
        toast.success(`Оффер ${offer.code} деактивирован`);
        onOpenChange(false);
      }}
    />
  );
}

// ─── PageHeader ────────────────────────────────────────────────────────────────

function OffersHeader({
  count,
  action,
}: {
  count: number | null;
  action?: React.ReactNode;
}) {
  return (
    <PageHeader
      eyebrowNum="02"
      eyebrow="CATALOG · ОФФЕРЫ"
      title="Офферы"
      action={action}
      subtitle={
        count !== null ? (
          <>
            <span className="text-bg-11 font-medium">{count}</span>
            <HeaderSep />
            {pluralizeOffers(count)} в каталоге
          </>
        ) : undefined
      }
    />
  );
}

function pluralizeOffers(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "оффер";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "оффера";
  return "офферов";
}
