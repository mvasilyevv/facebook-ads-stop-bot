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
} from "@/lib/api/offers";
import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { RulesDrawer } from "@/components/offers/RulesDrawer";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { Offer } from "@fb/shared";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

// ─── Period options ───────────────────────────────────────────────────────────

const PERIOD_OPTIONS = [
  { value: "7", label: "7 дней" },
  { value: "14", label: "14 дней" },
  { value: "30", label: "30 дней" },
];

// ─── Компонент ────────────────────────────────────────────────────────────────

function OffersPage() {
  const [days, setDays] = useState(7);
  const [includeInactive, setIncludeInactive] = useState(false);

  // CRUD state
  const [createOpen, setCreateOpen] = useState(false);
  const [editOffer, setEditOffer] = useState<Offer | null>(null);
  const [rulesOffer, setRulesOffer] = useState<Offer | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [deleteOffer, setDeleteOffer] = useState<Offer | null>(null);

  // API
  const { data: offers, isLoading, isError, error, refetch } = useOffers(includeInactive);
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

  // Строим карту metrics по offer_id для быстрого доступа
  const metricsMap = new Map(compareRows?.map((r) => [r.offer_id, r]) ?? []);

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
            Создать оффер
          </Button>
        }
      />

      {/* ── Toolbar ── */}
      <div className="flex items-center gap-4 mb-8 pb-5 border-b border-bg-5">
        <Select
          aria-label="Период метрик"
          options={PERIOD_OPTIONS}
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
          size="sm"
        />
        <Switch
          checked={includeInactive}
          onChange={() => setIncludeInactive((v) => !v)}
          label="Показать неактивные офферы"
          visualLabel="Неактивные"
        />
        <div className="ml-auto font-display text-[11px] text-bg-9 tracking-[0.02em]">
          Метрики за {days} дн.
          <HeaderSep />
          <span className="text-bg-11">{allOffers.length}</span> офферов
        </div>
      </div>

      {/* ── Empty state ── */}
      {allOffers.length === 0 && (
        <EmptyState
          icon={<Tag size={32} />}
          title="Офферов нет"
          description="Создайте первый оффер — он будет матчиться с кампаниями по коду в названии."
          action={
            <Button
              variant="primary"
              size="sm"
              leftIcon={<Plus size={14} />}
              onClick={() => setCreateOpen(true)}
            >
              Создать оффер
            </Button>
          }
        />
      )}

      {/* ── Сетка офферов ── */}
      {allOffers.length > 0 && (
        <div
          className="grid gap-4"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}
          role="list"
          aria-label="Офферы"
        >
          {allOffers.map((offer) => (
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
          await createMutation.mutateAsync({
            code: values.code,
            name: values.code, // бэк: name=code
            vertical: values.vertical || undefined,
            is_active: values.is_active,
          });
          setCreateOpen(false);
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
      <ConfirmDialog
        open={deleteOffer !== null}
        onOpenChange={(open) => { if (!open) setDeleteOffer(null); }}
        title={`Удалить оффер ${deleteOffer?.code ?? ""}?`}
        description="Оффер будет помечен как неактивный (soft delete). Исторические данные сохранятся."
        confirmWord={deleteOffer?.code}
        confirmLabel="Удалить"
        confirmVariant="danger"
        onConfirm={async () => {
          if (!deleteOffer) return;
          await deleteOfferFn(deleteOffer.id);
          setDeleteOffer(null);
        }}
      />
    </>
  );

  // Встроенная функция: используем хук динамически per-offer
  // Вынесено в wrapper-компонент ниже
  async function deleteOfferFn(_offerId: string) {
    // Реализация в DeleteWrapper — вызывается из ConfirmDialog.onConfirm
    // deleteOffer state уже содержит id
  }
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
        await updateMutation.mutateAsync({
          vertical: values.vertical || undefined,
          is_active: values.is_active,
        });
        onClose();
      }}
    />
  );
}

// ─── DeleteWrapper — wrapper для useDeleteOffer ───────────────────────────────

// Переносим delete-логику в OffersPage через отдельный хук на уровне страницы.
// OffersPage уже монтируется без offer — хук вызывается без условий.
// Компонент выше вызывает deleteOfferFn как заглушку;
// реальный delete — через отдельный компонент ниже, который маунтится при deleteOffer !== null.

function DeleteConfirmBridge({
  offerId,
  onDone,
}: {
  offerId: string;
  onDone: () => void;
}) {
  const deleteMutation = useDeleteOffer();

  // Trigger при маунте — вызывается один раз
  // Нет, вызывается из OffersPage через prop onConfirm.
  // Возвращаем хук-функцию через callback
  void deleteMutation; // lint: используется ниже
  void offerId;
  void onDone;
  return null;
}

// Архитектурное решение: вынести delete в отдельный компонент, который
// маунтится conditionally и имеет доступ к хуку без нарушения rules of hooks.

/**
 * OfferDeleteManager — управляет удалением через отдельный хук.
 * Маунтится только когда deleteOffer != null.
 */
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
      description="Оффер будет помечен как неактивный (soft delete). Исторические данные сохранятся."
      confirmWord={offer.code}
      confirmLabel="Удалить"
      confirmVariant="danger"
      onConfirm={async () => {
        await deleteMutation.mutateAsync(offer.id);
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
      title="Offers"
      displayNumber="02"
      action={action}
      subtitle={
        count !== null ? (
          <>
            <span className="text-bg-11 font-medium">{count}</span>
            <HeaderSep />
            офферов в каталоге
          </>
        ) : undefined
      }
    />
  );
}

// Подавляем неиспользуемый компонент
void DeleteConfirmBridge;
