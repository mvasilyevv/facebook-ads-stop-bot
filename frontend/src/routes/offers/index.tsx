/**
 * Offers (`/offers`) — каталог офферов.
 *
 * Блоки:
 *   1. PageHeader — eyebrow "03 / OFFERS", title "Offers.", кнопка "+ New offer".
 *   2. Compare-bar — selector периода (7 / 14 / 30 дней).
 *   3. Toggle "Включить неактивные".
 *   4. Grid карточек офферов с метриками за период.
 *   5. Modal создания/редактирования.
 *   6. RulesDrawer — редактор 6 порогов.
 *   7. ConfirmDialog — удаление (soft delete).
 *
 * Данные:
 *   - useOffers(include_inactive)
 *   - useOffersCompare(days)
 *
 * Известные ограничения (из CLAUDE.md):
 *   - Offer не имеет полей country_code / use_vision_creator / notes.
 *   - OfferRule — числовые поля, не JSONB.
 */

import { useState, useMemo } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Plus, Tag } from "lucide-react";

import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

import { OfferCard } from "@/components/offers/OfferCard";
import { OfferFormModal } from "@/components/offers/OfferFormModal";
import { RulesDrawer } from "@/components/offers/RulesDrawer";
import { SensitivityDrawer } from "@/components/offers/SensitivityDrawer";

import { useOffers, useOffersCompare, useDeleteOffer } from "@/lib/api/offers";
import { toast } from "@/components/ui/Toast";
import { formatSpend } from "@/lib/utils/format";
import type { Offer, OfferCompareRow } from "@/lib/types/api";

export const Route = createFileRoute("/offers/")({
  component: OffersPage,
});

const PERIOD_OPTIONS = [
  { value: "7", label: "7 дней" },
  { value: "14", label: "14 дней" },
  { value: "30", label: "30 дней" },
];

function OffersPage() {
  const [days, setDays] = useState(7);
  const [includeInactive, setIncludeInactive] = useState(false);

  // Состояния модальных окон.
  const [formOpen, setFormOpen] = useState(false);
  const [editOffer, setEditOffer] = useState<Offer | null>(null);
  const [rulesOffer, setRulesOffer] = useState<Offer | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [sensitivityOffer, setSensitivityOffer] = useState<Offer | null>(null);
  const [sensitivityOpen, setSensitivityOpen] = useState(false);
  const [deleteOffer, setDeleteOffer] = useState<Offer | null>(null);

  const offersQuery = useOffers(includeInactive);
  const compareQuery = useOffersCompare(days);
  const deleteMutation = useDeleteOffer();

  // Индекс метрик по offer_id для быстрого доступа.
  const metricsById = useMemo(() => {
    const map = new Map<string, OfferCompareRow>();
    for (const row of compareQuery.data ?? []) {
      map.set(row.offer_id, row);
    }
    return map;
  }, [compareQuery.data]);

  // Сортируем по spend (DESC), неактивные — в конец.
  const sortedOffers = useMemo(() => {
    if (!offersQuery.data) return [];
    return [...offersQuery.data].sort((a, b) => {
      // Неактивные вниз.
      if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
      const spendA = Number.parseFloat(metricsById.get(a.id)?.spend ?? "0");
      const spendB = Number.parseFloat(metricsById.get(b.id)?.spend ?? "0");
      return spendB - spendA;
    });
  }, [offersQuery.data, metricsById]);

  // Суммарные метрики для subtitle.
  const totalSpend = useMemo(() => {
    if (!compareQuery.data) return null;
    const sum = compareQuery.data.reduce(
      (acc, r) => acc + Number.parseFloat(r.spend ?? "0"),
      0,
    );
    return sum;
  }, [compareQuery.data]);

  function openCreate() {
    setEditOffer(null);
    setFormOpen(true);
  }

  function openEdit(offer: Offer) {
    setEditOffer(offer);
    setFormOpen(true);
  }

  function openRules(offer: Offer) {
    setRulesOffer(offer);
    setRulesOpen(true);
  }

  function openSensitivity(offer: Offer) {
    setSensitivityOffer(offer);
    setSensitivityOpen(true);
  }

  function handleDelete() {
    if (!deleteOffer) return;
    deleteMutation.mutate(deleteOffer.id, {
      onSuccess: () => {
        toast.success(
          "Оффер деактивирован",
          `${deleteOffer.code} переведён в неактивный статус.`,
        );
        setDeleteOffer(null);
      },
      onError: (err) =>
        toast.error("Ошибка удаления", err instanceof Error ? err.message : String(err)),
    });
  }

  const activeCount = offersQuery.data?.filter((o) => o.is_active).length ?? 0;
  const inactiveCount = offersQuery.data?.filter((o) => !o.is_active).length ?? 0;

  return (
    <>
      {/* 1. Заголовок страницы */}
      <PageHeader
        eyebrowNum="03"
        eyebrow="ОФФЕРЫ"
        title="Офферы"
        displayNumber="03"
        subtitle={
          offersQuery.data ? (
            <>
              <span>{activeCount} активных</span>
              {inactiveCount > 0 ? (
                <>
                  <HeaderSep />
                  <span className="text-bg-8">{inactiveCount} неактивных</span>
                </>
              ) : null}
              {totalSpend != null ? (
                <>
                  <HeaderSep />
                  <span>
                    {formatSpend(totalSpend)} за {days}д
                  </span>
                </>
              ) : null}
            </>
          ) : (
            "Загрузка..."
          )
        }
        action={
          <Button
            variant="primary"
            leftIcon={<Plus size={14} aria-hidden="true" />}
            onClick={openCreate}
          >
            Новый оффер
          </Button>
        }
      />

      {/* 2 & 3. Панель фильтров */}
      <div className="flex items-center gap-4 mb-6">
        <Select
          id="period-select"
          options={PERIOD_OPTIONS}
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
          size="sm"
        />
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            className="size-4 accent-accent"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
          />
          <span className="text-[13px] text-bg-10">Показать неактивные</span>
        </label>
      </div>

      {/* 4. Основной контент */}
      {offersQuery.isLoading ? (
        <OffersSkeletonGrid />
      ) : offersQuery.isError ? (
        <ErrorState
          title="Не удалось загрузить офферы"
          error={offersQuery.error}
          onRetry={() => offersQuery.refetch()}
        />
      ) : sortedOffers.length === 0 ? (
        <EmptyState
          icon={<Tag size={40} strokeWidth={1.25} aria-hidden="true" />}
          title="Офферов нет"
          description="Создайте первый оффер, чтобы привязать правила и отслеживать метрики объявлений."
          action={
            <Button
              variant="primary"
              leftIcon={<Plus size={14} aria-hidden="true" />}
              onClick={openCreate}
            >
              Новый оффер
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {sortedOffers.map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              metrics={metricsById.get(offer.id)}
              onEdit={openEdit}
              onDelete={(o) => setDeleteOffer(o)}
              onRules={openRules}
              onSensitivity={openSensitivity}
            />
          ))}
        </div>
      )}

      {/* 5. Modal создания / редактирования */}
      <OfferFormModal
        open={formOpen}
        onOpenChange={setFormOpen}
        editOffer={editOffer}
      />

      {/* 6. Rules Drawer */}
      <RulesDrawer
        open={rulesOpen}
        onOpenChange={setRulesOpen}
        offer={rulesOffer}
      />

      {/* 6b. Sensitivity Drawer */}
      <SensitivityDrawer
        open={sensitivityOpen}
        onOpenChange={setSensitivityOpen}
        offer={sensitivityOffer}
      />

      {/* 7. ConfirmDialog удаления */}
      <ConfirmDialog
        open={!!deleteOffer}
        onOpenChange={(open) => {
          if (!open) setDeleteOffer(null);
        }}
        title={`Деактивировать ${deleteOffer?.code ?? "оффер"}?`}
        description={`Оффер "${deleteOffer?.name ?? ""}" будет переведён в неактивный статус. Связанные объявления останутся без изменений.`}
        confirmLabel="Деактивировать"
        cancelLabel="Отмена"
        onConfirm={handleDelete}
      />
    </>
  );
}

/** Skeleton-grid пока загружаются офферы. */
function OffersSkeletonGrid() {
  return (
    <div className="grid grid-cols-3 gap-4">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="border border-bg-5 bg-bg-1 p-6 flex flex-col gap-5"
        >
          <div className="flex flex-col gap-2">
            <Skeleton height={18} width={80} />
            <Skeleton height={13} width="70%" />
          </div>
          <div className="grid grid-cols-4 gap-3 border-t border-bg-5 pt-4">
            {[0, 1, 2, 3].map((j) => (
              <div key={j} className="flex flex-col gap-1">
                <Skeleton height={10} />
                <Skeleton height={14} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
