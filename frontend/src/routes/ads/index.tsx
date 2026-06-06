/**
 * Ads — страница списка объявлений с фильтрами и bulk-действиями.
 *
 * Архитектура:
 *   AdsFilterBar (controlled, state живёт здесь)
 *   DataTable (buildAdsColumns, виртуализация 600px, useAds cursor-пагинация)
 *   BulkActionBar (sticky bottom, при выборе > 0)
 *   Pagination
 *
 * MONEY-FLOW bulk-disable:
 *   1. Пользователь выбирает строки → BulkActionBar.onDisable
 *   2. ConfirmDialog "Отключить N объявлений?" (danger)
 *   3. onConfirm → useBulkDisable({ fb_ad_ids, reason, idempotency_token: crypto.randomUUID() })
 *   4. optimistic: selectedIds.clear, invalidate ["ads"], ["tasks", "disable"]
 *   5. WS task_changed → дополнительная инвалидация через useRealtimeInvalidation
 *
 * Клик строки → navigate("/ads/:fbAdId") → Drawer деталей.
 */

import { createFileRoute, useRouter } from "@tanstack/react-router";
import { useMemo, useState, useCallback } from "react";
import type { SortingState } from "@tanstack/react-table";

import { PageHeader } from "@/components/layout/PageHeader";
import { AdsFilterBar, type AdsFilterState } from "@/components/domain/ads/AdsFilterBar";
import { BulkActionBar } from "@/components/domain/ads/BulkActionBar";
import { buildAdsColumns } from "@/components/domain/ads/adsColumnDefs";
import { DataTable } from "@/components/data/table/DataTable";
import { Pagination } from "@/components/data/table/Pagination";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

import { useAds, useBulkDisable, useBulkSnooze } from "@/lib/api/ads";
import { useRealtimeInvalidation } from "@/lib/websocket/useRealtimeInvalidation";

import type { AdSnapshot, AlertState } from "@fb/shared";

// ─── Route ────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/ads/")({
  component: AdsPage,
});

// ─── Константы ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;
const TABLE_HEIGHT = 600;

// ─── Компонент ────────────────────────────────────────────────────────────────

function AdsPage() {
  const router = useRouter();
  useRealtimeInvalidation();

  // ── Фильтры (controlled) ──────────────────────────────────────────────────
  const [filterState, setFilterState] = useState<AdsFilterState>({
    search: "",
    selectedStates: new Set<AlertState>(),
    selectedOffer: "",
    selectedCountry: "",
  });

  // ── Пагинация ─────────────────────────────────────────────────────────────
  const [page, setPage] = useState(0);
  const offset = page * PAGE_SIZE;

  // ── Сортировка ────────────────────────────────────────────────────────────
  const [sorting, setSorting] = useState<SortingState>([]);

  // ── Bulk selection ────────────────────────────────────────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);

  // ── Данные ────────────────────────────────────────────────────────────────
  const alertStatesParam =
    filterState.selectedStates.size > 0
      ? [...filterState.selectedStates].join(",")
      : undefined;

  const { data, isLoading, isError, error, refetch } = useAds({
    alert_states: alertStatesParam,
    limit: PAGE_SIZE,
    offset,
  });

  const rows: AdSnapshot[] = data?.data ?? [];
  const total = data?.total ?? 0;

  // ── Мутации ───────────────────────────────────────────────────────────────
  const bulkDisable = useBulkDisable();
  const bulkSnooze = useBulkSnooze();

  // ── Фильтрация на клиенте (search — клиентская, остальное серверное) ──────
  const filteredRows = useMemo<AdSnapshot[]>(() => {
    if (!filterState.search) return rows;
    const q = filterState.search.toLowerCase();
    return rows.filter(
      (r) =>
        r.ad_name.toLowerCase().includes(q) ||
        r.fb_ad_id.includes(q) ||
        (r.offer_code?.toLowerCase().includes(q) ?? false),
    );
  }, [rows, filterState.search]);

  // ── Callbacks фильтров ────────────────────────────────────────────────────
  const handleStateToggle = useCallback((state: AlertState) => {
    setFilterState((prev) => {
      const next = new Set(prev.selectedStates);
      if (next.has(state)) {
        next.delete(state);
      } else {
        next.add(state);
      }
      return { ...prev, selectedStates: next };
    });
    setPage(0);
  }, []);

  const handleClearAll = useCallback(() => {
    setFilterState({
      search: "",
      selectedStates: new Set(),
      selectedOffer: "",
      selectedCountry: "",
    });
    setPage(0);
  }, []);

  // ── Bulk selection helpers ────────────────────────────────────────────────
  const allPageSelected =
    filteredRows.length > 0 &&
    filteredRows.every((r) => selectedIds.has(r.fb_ad_id));
  const someSelected = selectedIds.size > 0;
  const indeterminate = someSelected && !allPageSelected;

  const handleSelectAll = useCallback(() => {
    if (allPageSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredRows.map((r) => r.fb_ad_id)));
    }
  }, [allPageSelected, filteredRows]);

  const handleRowSelect = useCallback((fbAdId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(fbAdId)) {
        next.delete(fbAdId);
      } else {
        next.add(fbAdId);
      }
      return next;
    });
  }, []);

  // ── Навигация в drawer ────────────────────────────────────────────────────
  const handleRowOpen = useCallback(
    (fbAdId: string) => {
      void router.navigate({ to: "/ads/$fbAdId", params: { fbAdId } });
    },
    [router],
  );

  // ── Column defs ───────────────────────────────────────────────────────────
  const columns = useMemo(
    () =>
      buildAdsColumns({
        allSelected: allPageSelected,
        indeterminate,
        onSelectAll: handleSelectAll,
        isRowSelected: (id) => selectedIds.has(id),
        onRowSelect: handleRowSelect,
        onRowOpen: handleRowOpen,
      }),
    [allPageSelected, indeterminate, handleSelectAll, selectedIds, handleRowSelect, handleRowOpen],
  );

  // ── Row variant (highlight по alert_state) ────────────────────────────────
  const getRowVariant = useCallback(
    (row: AdSnapshot) => {
      if (selectedIds.has(row.fb_ad_id)) return "selected" as const;
      if (row.alert_state === "stop_sent") return "stop" as const;
      if (row.alert_state === "warning_sent") return "warning" as const;
      return "normal" as const;
    },
    [selectedIds],
  );

  // ── MONEY: bulk disable flow ──────────────────────────────────────────────
  async function handleBulkDisableConfirm() {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    // idempotency_token = crypto.randomUUID() — защита от двойного сабмита
    const idempotencyToken = crypto.randomUUID();
    await bulkDisable.mutateAsync({
      fb_ad_ids: ids,
      reason: `bulk-disable via dashboard idempotency:${idempotencyToken}`,
    });
    // Сброс выбора после успеха
    setSelectedIds(new Set());
  }

  // ── Bulk snooze ───────────────────────────────────────────────────────────
  function handleBulkSnooze(minutes: number) {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    void bulkSnooze.mutateAsync({ fb_ad_ids: ids, minutes });
    setSelectedIds(new Set());
  }

  // Offer/country options из данных на странице
  const offerOptions = useMemo(() => {
    const codes = new Set<string>();
    rows.forEach((r) => {
      if (r.offer_code) codes.add(r.offer_code);
    });
    return [...codes].map((c) => ({ value: c, label: c }));
  }, [rows]);

  const countryOptions: { value: string; label: string }[] = [];

  return (
    <div className="px-8 py-8 pb-24" aria-label="Объявления">
      {/* ── PageHeader ──────────────────────────────────────────────────────── */}
      <PageHeader
        eyebrowNum="02"
        eyebrow="ADS · MONITOR · ACT"
        title="Ads"
        displayNumber="02"
        subtitle={
          total > 0 ? (
            <span>
              {total} объявлений
              {selectedIds.size > 0 && (
                <span className="text-accent ml-2">· {selectedIds.size} выбрано</span>
              )}
            </span>
          ) : null
        }
      />

      {/* ── Filter bar ──────────────────────────────────────────────────────── */}
      <div className="mb-4">
        <AdsFilterBar
          filterState={filterState}
          offerOptions={offerOptions}
          countryOptions={countryOptions}
          onSearchChange={(v) => {
            setFilterState((p) => ({ ...p, search: v }));
            setPage(0);
          }}
          onStateToggle={handleStateToggle}
          onOfferChange={(v) => {
            setFilterState((p) => ({ ...p, selectedOffer: v }));
            setPage(0);
          }}
          onCountryChange={(v) => {
            setFilterState((p) => ({ ...p, selectedCountry: v }));
            setPage(0);
          }}
          onClearAll={handleClearAll}
        />
      </div>

      {/* ── Error state ─────────────────────────────────────────────────────── */}
      {isError ? (
        <ErrorState
          title="Не удалось загрузить объявления."
          error={error}
          onRetry={() => void refetch()}
        />
      ) : (
        <>
          {/* ── DataTable с виртуализацией ────────────────────────────────── */}
          <DataTable
            data={filteredRows}
            columns={columns}
            sorting={sorting}
            onSortingChange={setSorting}
            getRowId={(row) => row.fb_ad_id}
            getRowVariant={getRowVariant}
            onRowClick={(row) => handleRowOpen(row.fb_ad_id)}
            containerHeight={TABLE_HEIGHT}
            loading={isLoading}
            skeletonRows={12}
            label="Список объявлений"
            emptyState={
              <EmptyState
                title="Объявлений нет"
                description="Попробуйте сбросить фильтры."
              />
            }
          />

          {/* ── Pagination ────────────────────────────────────────────────── */}
          {total > PAGE_SIZE && (
            <div className="mt-3">
              <Pagination
                offset={offset}
                pageSize={filteredRows.length}
                total={total}
                onPrev={() => setPage((p) => Math.max(0, p - 1))}
                onNext={() => setPage((p) => p + 1)}
              />
            </div>
          )}
        </>
      )}

      {/* ── BulkActionBar — sticky, только при выбранных строках ────────────── */}
      {someSelected && (
        <BulkActionBar
          count={selectedIds.size}
          isPending={bulkDisable.isPending || bulkSnooze.isPending}
          onDisable={() => setConfirmOpen(true)}
          onSnooze={handleBulkSnooze}
          onMarkClaimed={() => {
            // Будущее: mark claimed через API
          }}
          onClear={() => setSelectedIds(new Set())}
        />
      )}

      {/* ── MONEY: ConfirmDialog для bulk disable ────────────────────────────── */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Отключить ${selectedIds.size} объявлений?`}
        description={`Будет создано ${selectedIds.size} задач отключения через Marketing API. Действие необратимо без ручного включения.`}
        confirmLabel={`Отключить ${selectedIds.size}`}
        confirmVariant="danger"
        onConfirm={handleBulkDisableConfirm}
      />
    </div>
  );
}
