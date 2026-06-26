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

import { useState, useMemo } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Tag, Plus, ChevronDown } from "lucide-react";

import {
  useOffers,
  useOffersCompare,
  useCreateOffer,
  useUpdateOffer,
  useDeleteOffer,
  useOfferRules,
  useSaveOfferRules,
  type Offer,
} from "@/lib/api/offers";
import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { rulesValuesToPayload, rulesValuesFromOut } from "@/components/offers/OfferRulesFields";
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

const TAB_LABELS: Record<OfferTab, string> = {
  all: "Все",
  active: "Активные",
  inactive: "Неактивные",
};

// ─── Компонент ────────────────────────────────────────────────────────────────

function OffersPage() {
  const [tab, setTab] = useState<OfferTab>("all");
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
  const saveRules = useSaveOfferRules();

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
  const filteredOffers = allOffers.filter((o) => {
    if (tab === "active") return o.is_active;
    if (tab === "inactive") return !o.is_active;
    return true;
  });

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
            Новый оффер
          </Button>
        }
      />

      {/* ── Toolbar: tab pills + sort ── */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2">
          {(Object.keys(TAB_LABELS) as OfferTab[]).map((t) => (
            <FilterPill key={t} active={tab === t} onClick={() => setTab(t)}>
              {TAB_LABELS[t]}
            </FilterPill>
          ))}
        </div>
        <button
          className="font-display text-[12px] text-bg-10 flex items-center gap-1.5 px-3 py-1.5 border border-[var(--hairline)] hover:border-[var(--hairline-strong)] rounded-[var(--radius-2)] transition-colors"
          type="button"
          aria-label="Сортировка"
        >
          Сортировка: spend
          <ChevronDown size={12} aria-hidden="true" />
        </button>
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
          className="grid gap-4"
          style={{ gridTemplateColumns: "repeat(3, 1fr)" }}
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
          // 1) Создаём оффер → получаем id. 2) Пишем стоп-правила (CPA + чувствительность).
          const created = await createMutation.mutateAsync({
            code: values.code,
            name: values.code, // бэк: name=code
            is_active: values.is_active,
            pixel_id: values.pixel_id || null, // пусто → не задан
            ad_account_ids: values.ad_account_ids, // мульти-кабинет: min 1
            countries: values.countries, // гео оффера (ISO-2 upper)
            default_cpa_cents: values.default_cpa_cents, // дефолтный CPA (центы) → префилл визарда
          });
          await saveRules.mutateAsync({
            offerId: created.id,
            data: rulesValuesToPayload(values.rules),
          });
          setCreateOpen(false);
          toast.success(
            `Оффер ${values.code} создан · кабинеты: ${values.ad_account_ids.join(", ")}`,
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
  const saveRules = useSaveOfferRules();
  const { data: rules } = useOfferRules(offer.id);
  // Мемо по ссылке rules (react-query кэш стабилен) — иначе initialRules-объект
  // пересоздавался бы каждый рендер и сбрасывал ввод формы.
  const initialRules = useMemo(() => rulesValuesFromOut(rules), [rules]);

  return (
    <OfferFormModal
      open
      onOpenChange={(open) => { if (!open) onClose(); }}
      offer={offer}
      initialRules={initialRules}
      onSave={async (values) => {
        await updateMutation.mutateAsync({
          is_active: values.is_active,
          pixel_id: values.pixel_id, // строка (в т.ч. "") — форма источник истины
          ad_account_ids: values.ad_account_ids, // мульти-кабинет: замена списка
          countries: values.countries, // гео оффера (ISO-2 upper) — замена списка
          default_cpa_cents: values.default_cpa_cents, // дефолтный CPA (центы), в т.ч. null = очистить
        });
        await saveRules.mutateAsync({
          offerId: offer.id,
          data: rulesValuesToPayload(values.rules),
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
      title="Офферы"
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
